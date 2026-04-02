"""Quartz DB client that conforms to the DatabaseInterface."""

import datetime as dt
import logging
import os
from uuid import UUID, uuid4

import pandas as pd
import sentry_sdk
from fastapi import HTTPException
from pvsite_datamodel import DatabaseConnection
from pvsite_datamodel.pydantic_models import PVSiteEditMetadata
from pvsite_datamodel.read import (
    get_latest_forecast_values_by_site,
    get_pv_generation_by_sites,
    get_site_by_uuid,
    get_sites_by_country,
    get_sites_from_user,
    get_user_by_email,
)
from pvsite_datamodel.sqlmodels import ForecastValueSQL, GenerationSQL, LocationAssetType
from pvsite_datamodel.write.database import save_api_call_to_db
from pvsite_datamodel.write.generation import insert_generation_values
from pvsite_datamodel.write.user_and_site import edit_site
from sqlalchemy.orm import Session
from typing_extensions import override

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import EMAIL_KEY

log = logging.getLogger(__name__)


class StorageClient(models.StorageInterface):
    """Client for the nowcasting databases that conforms to the StorageInterface."""

    connection: DatabaseConnection
    session: Session | None = None

    def __init__(self, database_url: str) -> None:
        """Initialize the client with a SQLAlchemy database connection and session."""
        self.connection = DatabaseConnection(url=database_url, echo=False)

    def _get_session(self) -> Session:
        """Allows for overriding the default session (useful for testing)."""
        if self.session is None:
            return self.connection.get_session()
        else:
            return self.session

    @override
    def get_predicted_generation(
        self,
        location_uuid: UUID | str,
        window_start: dt.datetime,
        window_end: dt.datetime,
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
        created_cutoff: dt.datetime | None = None,
        forecast_horizon_minutes: int = 0,
        forecaster_name: str | None = None,
        forecaster_version: str | None = None,
    ) -> list[models.PredictedGenerationValue]:
        if forecast_horizon_minutes == 60 * 24:  # Use DA forecast when a day's horizon is specified
            # Fetch the day ahead forecast
            day_ahead_hours: int | None = 9
            day_ahead_timezone_delta_hours: float | None = 5.5
        else:
            day_ahead_hours = None
            day_ahead_timezone_delta_hours = None

        with self._get_session() as session:
            out: list[models.PredictedGenerationValue] = []
            match energy_type, location_type, location_uuid:
                case _, models.LocationType.REGION, UUID():
                    raise HTTPException(
                        status_code=400,
                        detail="Region location type must use string location",
                    )
                case _, models.LocationType.REGION, str():
                    # The regional fetch is the only one which requires string-locations
                    # This case mirrors the old _get_predicted_power_production_for_location func
                    if energy_type == models.EnergyType.SOLAR:
                        asset_type = LocationAssetType.pv
                        forecaster_name = "pvnet_india"
                    else:
                        asset_type = LocationAssetType.wind
                        forecaster_name = "windnet_india_adjust"

                    sites = [
                        s
                        for s in get_sites_by_country(session, country="india", client_name="")
                        if (s.asset_type == asset_type) and (s.region == location_uuid)
                    ]

                    if len(sites) == 0:
                        raise HTTPException(
                            status_code=204,
                            detail=f"Site for {location_uuid} not found and {asset_type} not found",
                        )

                    site = sites[0]

                    if site.ml_model is not None:
                        forecaster_name = site.ml_model.name
                    log.info(f"Using ml model {forecaster_name}")
                    loc_uuid: UUID = (
                        site.location_uuid
                        if isinstance(site.location_uuid, UUID)
                        else UUID(site.location_uuid)
                    )
                    loc_cap_kw: float = float(site.capacity_kw)

                case _, models.LocationType.SITE, UUID():
                    # This case mirrors the old get_site_forecast function
                    forecaster_name = "pvnet_ad_sites"
                    check_user_has_access_to_site(
                        session=session,
                        email=authdata[EMAIL_KEY],
                        site_uuid=location_uuid,
                    )
                    site = get_site_by_uuid(session=session, site_uuid=str(location_uuid))
                    if site.ml_model is not None:
                        forecaster_name = site.ml_model.name
                    log.info(f"Using ml model {forecaster_name}")
                    loc_uuid = (
                        site.location_uuid
                        if isinstance(site.location_uuid, UUID)
                        else UUID(site.location_uuid)
                    )
                    loc_cap_kw = float(site.capacity_kw)

                case _, _, str():
                    raise HTTPException(
                        status_code=400,
                        detail="Site location type must use UUID location",
                    )
                case _:
                    raise HTTPException(
                        status_code=400,
                        detail="Quartz Storage Client only supports REGION and SITE location types",
                    )

            values = get_latest_forecast_values_by_site(
                session,
                site_uuids=[loc_uuid],
                start_utc=window_start,
                day_ahead_hours=day_ahead_hours,
                day_ahead_timezone_delta_hours=day_ahead_timezone_delta_hours,
                forecast_horizon_minutes=forecast_horizon_minutes
                if forecast_horizon_minutes > 0
                else None,
                model_name=forecaster_name,
            )
            if not isinstance(values, dict):
                raise HTTPException(
                    status_code=500,
                    detail="Unexpected error fetching forecast values from database",
                )
            forecast_values: list[ForecastValueSQL] = values[loc_uuid]

        out = [
            models.PredictedGenerationValue(
                power_kilowatts=int(value.forecast_power_kw)
                if value.forecast_power_kw >= 0
                else 0,  # From the old code. Not sure why we'd ever save negatives
                valid_timestamp=value.start_utc.replace(tzinfo=dt.UTC),
                location_uuid=loc_uuid,
                capacity_kilowatts=loc_cap_kw,
                created_timestamp=value.created_utc.replace(tzinfo=dt.UTC),
                init_timestamp=value.created_utc.replace(tzinfo=dt.UTC),
                forecaster_name=forecaster_name,
                forecaster_version=forecaster_version or "not-set",
            )
            for value in forecast_values
        ]

        return out

    @override
    def put_predicted_generation(
        self,
        generation_values: list[models.PredictedGenerationValue],
        location_type: models.LocationType,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
    ) -> None:
        match energy_type, location_type:
            case _, models.LocationType.SITE:
                loc_uuids: set[UUID] = set()
                for gen_value in generation_values:
                    loc_uuids.add(gen_value.location_uuid)
                if len(loc_uuids) != 1:
                    raise HTTPException(
                        status_code=400,
                        detail="All generation values must belong to the same location",
                    )
                with self._get_session() as session:
                    check_user_has_access_to_site(
                        session=session,
                        email=authdata[EMAIL_KEY],
                        site_uuid=generation_values[0].location_uuid,
                    )
                    generations: list[dict[str, dt.datetime | float | UUID]] = [
                        {
                            "start_utc": v.valid_timestamp,
                            "power_kw": v.power_kilowatts,
                            "site_uuid": v.location_uuid,
                        }
                        for v in generation_values
                    ]
                    generation_values_df = pd.DataFrame(generations)
                    capacity_factor = float(os.getenv("ERROR_GENERATION_CAPACITY_FACTOR", 1.1))
                    site = get_site_by_uuid(
                        session=session,
                        site_uuid=str(generation_values[0].location_uuid),
                    )
                    exceeded_capacity = generation_values_df[
                        generation_values_df["power_kw"] > float(site.capacity_kw) * capacity_factor
                    ]
                    if len(exceeded_capacity) > 0:
                        # alert Sentry and return 422 validation error
                        sentry_sdk.capture_message(
                            f"Error processing predicted generation values. "
                            f"One (or more) values are larger than {capacity_factor} "
                            f"times the site capacity of {site.capacity_kw} kWp. "
                            f"Site: {generation_values[0].location_uuid}",
                        )
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                f"Error processing predicted generation values. "
                                f"One (or more) values are larger than {capacity_factor} "
                                f"times the site capacity of {site.capacity_kw} kWp. "
                                "Please adjust this generation value, the site capacity, "
                                "or contact quartz.support@openclimatefix.org."
                            ),
                        )
                    insert_generation_values(session, generation_values_df)
                    session.commit()

            case _:
                raise HTTPException(
                    status_code=400,
                    detail="Quartz Storage Client only supports putting SITE location types",
                )

    @override
    def get_actual_generation(
        self,
        location_uuid: UUID | str,
        window_start: dt.datetime,
        window_end: dt.datetime,
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
        observer_name: str | None = None,
        created_cutoff: dt.datetime | None = None,
    ) -> list[models.ActualGenerationValue]:
        if energy_type == models.EnergyType.SOLAR:
            asset_type = LocationAssetType.pv
        else:
            asset_type = LocationAssetType.wind

        with self._get_session() as session:
            match energy_type, location_type, location_uuid:
                case _, models.LocationType.REGION, UUID():
                    raise HTTPException(
                        status_code=400,
                        detail="Region location type must use string location",
                    )
                case _, models.LocationType.REGION, str():
                    # This mirrors the old _get_generation_for_location function
                    sites = get_sites_by_country(session, country="india", client_name="")
                    sites = [s for s in sites if (s.asset_type == asset_type)]
                    loc_uuid: UUID = (
                        sites[0].location_uuid
                        if isinstance(sites[0].location_uuid, UUID)
                        else UUID(sites[0].location_uuid)
                    )
                    loc_cap_kw: float = float(sites[0].capacity_kw)
                case _, models.LocationType.SITE, UUID():
                    # This mirrors the old get_site_generation function
                    check_user_has_access_to_site(
                        session=session,
                        email=authdata[EMAIL_KEY],
                        site_uuid=location_uuid,
                    )
                    site = get_site_by_uuid(session=session, site_uuid=str(location_uuid))
                    loc_uuid = (
                        site.location_uuid
                        if isinstance(site.location_uuid, UUID)
                        else UUID(site.location_uuid)
                    )
                    loc_cap_kw = float(site.capacity_kw)
                case _, _, str():
                    raise HTTPException(
                        status_code=400,
                        detail="Site location type must use UUID location",
                    )
                case _:
                    raise HTTPException(
                        status_code=400,
                        detail="Quartz Storage Client only supports REGION and SITE location types",
                    )

            values = get_pv_generation_by_sites(
                session=session,
                site_uuids=[loc_uuid],
                start_utc=window_start,
                end_utc=window_end,
            )

            out = [
                models.ActualGenerationValue(
                    power_kilowatts=int(value.generation_power_kw)
                    if value.generation_power_kw >= 0
                    else 0,  # From the old code (see above)
                    valid_timestamp=value.start_utc.replace(tzinfo=dt.UTC),
                    location_uuid=loc_uuid,
                    capacity_kilowatts=loc_cap_kw,
                    observer_name="not-set",
                )
                for value in values
                if isinstance(value, GenerationSQL)
            ]

            return out

    @override
    def put_actual_generation(
        self,
        generation_values: list[models.ActualGenerationValue],
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
    ) -> None:
        loc_uuids: set[UUID] = set()
        for gen_value in generation_values:
            loc_uuids.add(gen_value.location_uuid)
        if len(loc_uuids) != 1:
            raise HTTPException(
                status_code=400,
                detail="All generation values must belong to the same location",
            )
        match energy_type, location_type:
            case _, models.LocationType.SITE:
                # This case mirrors the old post_site_generation function
                with self._get_session() as session:
                    check_user_has_access_to_site(
                        session=session,
                        email=authdata[EMAIL_KEY],
                        site_uuid=generation_values[0].location_uuid,
                    )
                    generations = [
                        {
                            "start_utc": v.valid_timestamp,
                            "power_kw": v.power_kilowatts,
                            "site_uuid": v.location_uuid,
                        }
                        for v in generation_values
                    ]
                    generation_values_df = pd.DataFrame(generations)
                    capacity_factor = float(os.getenv("ERROR_GENERATION_CAPACITY_FACTOR", 1.1))
                    site = get_site_by_uuid(session=session, site_uuid=str(next(iter(loc_uuids))))
                    exceeded_capacity = generation_values_df[
                        generation_values_df["power_kw"] > float(site.capacity_kw) * capacity_factor
                    ]
                    if len(exceeded_capacity) > 0:
                        # alert Sentry and return 422 validation error
                        sentry_sdk.capture_message(
                            f"Error processing actual generation values. "
                            f"One (or more) values are larger than {capacity_factor} "
                            f"times the site capacity of {site.capacity_kw} kWp. "
                            # f"User: {auth['https://openclimatefix.org/email']}"
                            f"Site: {generation_values[0].location_uuid}",
                        )
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                f"Error processing actual generation values. "
                                f"One (or more) values are larger than {capacity_factor} "
                                f"times the site capacity of {site.capacity_kw} kWp. "
                                "Please adjust this generation value, the site capacity, "
                                "or contact quartz.support@openclimatefix.org."
                            ),
                        )

                    insert_generation_values(session, generation_values_df)
                    session.commit()
            case _:
                raise HTTPException(
                    status_code=400,
                    detail="Quartz Storage Client only supports putting SITE location types",
                )

    @override
    def get_locations(
        self,
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
        location_uuid: UUID | None = None,
    ) -> list[models.Location]:
        locations: list[models.Location] = []
        match energy_type, location_type, location_uuid:
            case _, models.LocationType.REGION, None:
                # This case mirrors the old get_wind_regions and get_solar_regions functions
                locations = [
                    models.Location(
                        name="ruvnl",
                        # NOTE: All these parameters are fake as the regions route doesn't use them
                        # I don't like this, but it's only a problem so long as the quartzdb has to
                        # be supported.
                        uuid=uuid4(),
                        latitude=0.0,
                        longitude=0.0,
                        capacity_kilowatts=0.0,
                    ),
                ]
            case _, models.LocationType.SITE, None:
                # This case mirrors the old get_sites function
                with self._get_session() as session:
                    user = get_user_by_email(session, authdata[EMAIL_KEY])
                    sites_sql = get_sites_from_user(session, user=user)

                    locations = [
                        models.Location(
                            uuid=site_sql.location_uuid,
                            name=site_sql.client_location_name,
                            latitude=site_sql.latitude,
                            longitude=site_sql.longitude,
                            capacity_kilowatts=site_sql.capacity_kw,
                            # Add all the legacy information as metadata
                            metadata={
                                k: v
                                for k, v in {
                                    "ml_id": site_sql.ml_id,
                                    "active": site_sql.active,
                                    "tilt": site_sql.tilt,
                                    "orientation": site_sql.orientation,
                                    "client_site_name": site_sql.client_location_name,
                                    "client_site_id": site_sql.client_location_id,
                                    "country": site_sql.country,
                                    "client_uuid": site_sql.client_uuid,
                                    "ml_model_uuid": site_sql.ml_model_uuid,
                                    "region": site_sql.region,
                                }.items()
                                if v is not None
                            },
                        )
                        for site_sql in sites_sql
                    ]
            case _, models.LocationType.SITE, UUID():
                raise NotImplementedError(
                    "Quartz Storage Client does not support fetching single SITE locations.",
                )
            case _:
                raise HTTPException(
                    status_code=400,
                    detail="Quartz Storage Client only supports getting "
                    "REGION and SITE location types",
                )

        return locations

    @override
    def get_predicted_generation_snapshot(
        self,
        location_uuids: list[UUID],
        snapshot_timestamp_utc: dt.datetime,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
        forecaster_name: str | None = None,
        forecaster_version: str | None = None,
    ) -> list[models.PredictedGenerationValue]:
        raise NotImplementedError("Quartz Storage Client does not support snapshot fetches.")

    @override
    def get_actual_generation_snapshot(
        self,
        location_uuids: list[UUID],
        snapshot_timestamp_utc: dt.datetime,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
        observer_name: str | None = None,
    ) -> list[models.ActualGenerationValue]:
        raise NotImplementedError("Quartz Storage Client does not support snapshot fetches.")

    @override
    def put_location(
        self,
        location: models.Location,
        location_type: models.LocationType,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
    ) -> models.Location:
        match energy_type, location_type:
            case _, models.LocationType.SITE:
                # This case mirrors the old put_site function
                with self._get_session() as session:
                    user = get_user_by_email(session, authdata[EMAIL_KEY])
                    site = get_site_by_uuid(session, str(location.uuid))
                    site_uuid = (
                        site.location_uuid
                        if isinstance(site.location_uuid, UUID)
                        else UUID(site.location_uuid)
                    )
                    check_user_has_access_to_site(session, authdata[EMAIL_KEY], site_uuid)

                    site_info = PVSiteEditMetadata(
                        latitude=location.latitude,
                        longitude=location.longitude,
                        capacity_kw=location.capacity_kilowatts,
                        client_site_id=location.metadata.get("client_site_id", None),
                        client_site_name=location.metadata.get("client_site_name", None),
                        orientation=location.metadata.get("orientation", None),
                        tilt=location.metadata.get("tilt", None),
                        inverter_capacity_kw=location.metadata.get("inverter_capacity_kw", None),
                        module_capacity_kw=location.metadata.get("module_capacity_kw", None),
                        dno=location.metadata.get("dno", None),
                        gsp=location.metadata.get("gsp", None),
                        client_uuid=location.metadata.get("client_uuid", None),
                    )
                    site, _ = edit_site(
                        session=session,
                        site_uuid=str(location.uuid),
                        site_info=site_info,
                        user_uuid=user.user_uuid,
                    )

                    return models.Location(
                        uuid=site_uuid,
                        name=site.client_location_name,
                        latitude=float(site.latitude),
                        longitude=float(site.longitude),
                        capacity_kilowatts=float(site.capacity_kw),
                        metadata={
                            k: v
                            for k, v in {
                                "tilt": site.tilt,
                                "orientation": site.orientation,
                                "client_site_name": site.client_location_name,
                                "country": site.country,
                                "ml_id": site.ml_id,
                                "active": site.active,
                                "client_uuid": site.client_uuid,
                                "ml_model_uuid": site.ml_model_uuid,
                                "location_type": site.location_type,
                                "inverter_capacity_kw": site.inverter_capacity_kw,
                                "module_capacity_kw": site.module_capacity_kw,
                                "dno": site.dno,
                                "gsp": site.gsp,
                                "region": site.region,
                            }.items()
                            if v is not None
                        },
                    )
            case _:
                raise HTTPException(
                    status_code=400,
                    detail="Quartz Storage Client only supports putting SITE location types",
                )

    @override
    def log_api_call(
        self,
        url: str,
        authdata: dict[str, str],
    ) -> None:
        with self._get_session() as session:
            log.info(f"Saving API call ({url=}) to database")
            user = get_user_by_email(session, authdata[EMAIL_KEY])
            save_api_call_to_db(url=url, session=session, user=user)

def check_user_has_access_to_site(
    session: Session,
    email: str,
    site_uuid: UUID,
) -> None:
    """Checks if a user has access to a site."""
    user = get_user_by_email(session=session, email=email)
    site_uuids = [str(site.location_uuid) for site in user.location_group.locations]

    if str(site_uuid) not in site_uuids:
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden. User ({email}) "
            f"does not have access to this site {site_uuid!s}. "
            f"User has access to {[str(s) for s in site_uuids]}",
        )
