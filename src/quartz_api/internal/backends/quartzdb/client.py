"""Quartz DB client that conforms to the DatabaseInterface."""

import datetime as dt
import logging
import os
from uuid import UUID

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
from pvsite_datamodel.sqlmodels import ForecastValueSQL, LocationAssetType
from pvsite_datamodel.write.database import save_api_call_to_db
from pvsite_datamodel.write.generation import insert_generation_values
from pvsite_datamodel.write.user_and_site import edit_site
from sqlalchemy.orm import Session
from typing_extensions import override

from quartz_api.internal import models
from quartz_api.internal.backends.quartzdb.smooth import smooth_forecast
from quartz_api.internal.backends.utils import get_window
from quartz_api.internal.middleware.auth import EMAIL_KEY, AuthDependency

log = logging.getLogger(__name__)


class Client(models.DatabaseInterface):
    """Defines Quartz DB client that conforms to the DatabaseInterface."""

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
    async def save_api_call_to_db(self, url: str, authdata: dict[str, str]) -> None:
        with self._get_session() as session:
            # save the API call
            log.info(f"Saving API call ({url=}) to database")
            user = get_user_by_email(session, authdata[EMAIL_KEY])
            save_api_call_to_db(url=url, session=session, user=user)

    def _get_predicted_power_production_for_location(
        self,
        location: str,
        asset_type: LocationAssetType,
        ml_model_name: str,
        forecast_horizon: models.ForecastHorizon = models.ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[models.PredictedPower]:
        """Gets the predicted power production for a location, regardless of type."""
        # Get the window
        start, _ = get_window()

        # get house ahead forecast
        if forecast_horizon == models.ForecastHorizon.day_ahead:
            day_ahead_hours = 9
            day_ahead_timezone_delta_hours = 5.5
            forecast_horizon_minutes = None
        elif forecast_horizon == models.ForecastHorizon.horizon:
            day_ahead_hours = None
            day_ahead_timezone_delta_hours = None
        else:
            day_ahead_hours = None
            day_ahead_timezone_delta_hours = None
            forecast_horizon_minutes = None

        # get site uuid
        with self._get_session() as session:
            sites = get_sites_by_country(session, country="india", client_name=location)

            # just select wind site and region
            sites = [s for s in sites if (s.asset_type == asset_type) and (s.region == location)]

            if len(sites) == 0:
                raise HTTPException(
                    status_code=204,
                    detail=f"Site for {location=} not found and {asset_type=} not found",
                )

            site = sites[0]

            if site.ml_model is not None:
                ml_model_name = site.ml_model.name
            log.info(f"Using ml model {ml_model_name}")

            # read actual generations
            values = get_latest_forecast_values_by_site(
                session,
                site_uuids=[site.location_uuid],
                start_utc=start,
                day_ahead_hours=day_ahead_hours,
                day_ahead_timezone_delta_hours=day_ahead_timezone_delta_hours,
                forecast_horizon_minutes=forecast_horizon_minutes,
                model_name=ml_model_name,
            )
            forecast_values: list[ForecastValueSQL] = values[site.location_uuid]

        # convert ForecastValueSQL to PredictedPower
        out = [
            models.PredictedPower(
                PowerKW=int(value.forecast_power_kw)
                if value.forecast_power_kw >= 0
                else 0,  # Set negative values of PowerKW up to 0
                Time=value.start_utc.replace(tzinfo=dt.UTC),
                CreatedTime=value.created_utc.replace(tzinfo=dt.UTC),
            )
            for value in forecast_values
        ]

        # smooth the forecasts
        if smooth_flag:
            out = smooth_forecast(out)

        return out

    def _get_generation_for_location(
        self,
        location: str,
        asset_type: LocationAssetType,
    ) -> list[models.ActualPower]:
        """Gets the measured power production for a location."""
        # Get the window
        start, end = get_window()

        # get site uuid
        with self._get_session() as session:
            sites = get_sites_by_country(session, country="india", client_name=location)

            # just select wind site
            sites = [site for site in sites if site.asset_type == asset_type]
            site = sites[0]

            # read actual generations
            values = get_pv_generation_by_sites(
                session=session,
                site_uuids=[site.location_uuid],
                start_utc=start,
                end_utc=end,
            )

        # convert from GenerationSQL to ActualPower
        out = [
            models.ActualPower(
                PowerKW=int(value.generation_power_kw)
                if value.generation_power_kw >= 0
                else 0,  # Set negative values of PowerKW up to 0
                Time=value.start_utc.replace(tzinfo=dt.UTC),
            )
            for value in values
        ]

        return out

    @override
    async def get_predicted_solar_power_production_for_location(
        self,
        location: str,
        forecast_horizon: models.ForecastHorizon = models.ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[models.PredictedPower]:
        # set this to be hard coded for now
        model_name = "pvnet_india"

        return self._get_predicted_power_production_for_location(
            location=location,
            asset_type=LocationAssetType.pv,
            forecast_horizon=forecast_horizon,
            forecast_horizon_minutes=forecast_horizon_minutes,
            smooth_flag=smooth_flag,
            ml_model_name=model_name,
        )

    @override
    async def get_predicted_wind_power_production_for_location(
        self,
        location: str,
        forecast_horizon: models.ForecastHorizon = models.ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[models.PredictedPower]:
        # set this to be hard coded for now
        model_name = "windnet_india_adjust"

        return self._get_predicted_power_production_for_location(
            location=location,
            asset_type=LocationAssetType.wind,
            forecast_horizon=forecast_horizon,
            forecast_horizon_minutes=forecast_horizon_minutes,
            smooth_flag=smooth_flag,
            ml_model_name=model_name,
        )

    @override
    async def get_actual_solar_power_production_for_location(
        self,
        location: str,
    ) -> list[models.ActualPower]:
        return self._get_generation_for_location(location=location, asset_type=LocationAssetType.pv)

    @override
    async def get_actual_wind_power_production_for_location(
        self,
        location: str,
    ) -> list[models.ActualPower]:
        return self._get_generation_for_location(
            location=location,
            asset_type=LocationAssetType.wind,
        )

    @override
    async def get_wind_regions(self) -> list[str]:
        return ["ruvnl"]

    @override
    async def get_solar_regions(self) -> list[str]:
        return ["ruvnl"]

    @override
    async def get_sites(self, authdata: dict[str, str]) -> list[models.Site]:
        # get sites uuids from user
        with self._get_session() as session:
            user = get_user_by_email(session, authdata[EMAIL_KEY])
            sites_sql = get_sites_from_user(session, user=user)

            sites = []
            for site_sql in sites_sql:
                site = models.Site(
                    site_uuid=site_sql.location_uuid,
                    client_site_name=site_sql.client_location_name,
                    orientation=site_sql.orientation,
                    tilt=site_sql.tilt,
                    capacity_kw=site_sql.capacity_kw,
                    latitude=site_sql.latitude,
                    longitude=site_sql.longitude,
                )
                sites.append(site)

            return sites

    @override
    async def put_site(
        self,
        site_uuid: UUID,
        site_properties: models.SiteProperties,
        authdata: dict[str, str],
    ) -> models.Site:
        # get sites uuids from user
        with self._get_session() as session:
            user = get_user_by_email(session, authdata[EMAIL_KEY])
            site = get_site_by_uuid(session, str(site_uuid))
            check_user_has_access_to_site(session, authdata[EMAIL_KEY], site.location_uuid)

            site_dict = site_properties.model_dump(exclude_unset=True, exclude_none=False)

            site_info = PVSiteEditMetadata(**site_dict)

            site, _ = edit_site(
                session=session,
                site_uuid=str(site_uuid),
                site_info=site_info,
                user_uuid=user.user_uuid,
            )

            return site

    @override
    async def get_site_forecast(
        self,
        site_uuid: UUID,
        authdata: dict[str, str],
    ) -> list[models.PredictedPower]:
        # TODO feels like there is some duplicated code here which could be refactored

        # hard coded model name
        ml_model_name = "pvnet_ad_sites"

        # Get the window
        start, _ = get_window()

        with self._get_session() as session:
            check_user_has_access_to_site(
                session=session,
                email=authdata[EMAIL_KEY],
                site_uuid=site_uuid,
            )

            # get site and the get the ml model name
            site = get_site_by_uuid(session=session, site_uuid=str(site_uuid))
            if site.ml_model is not None:
                ml_model_name = site.ml_model.name
            log.info(f"Using ml model {ml_model_name}")

            values = get_latest_forecast_values_by_site(
                session,
                site_uuids=[site_uuid],
                start_utc=start,
                model_name=ml_model_name,
            )
            forecast_values: list[ForecastValueSQL] = values[site_uuid]

            # convert ForecastValueSQL to PredictedPower
        out = [
            models.PredictedPower(
                PowerKW=int(value.forecast_power_kw)
                if value.forecast_power_kw >= 0
                else 0,  # Set negative values of PowerKW up to 0
                Time=value.start_utc.replace(tzinfo=dt.UTC),
                CreatedTime=value.created_utc.replace(tzinfo=dt.UTC),
            )
            for value in forecast_values
        ]

        return out

    @override
    async def get_site_generation(
        self,
        site_uuid: UUID,
        authdata: dict[str, str],
    ) -> list[models.ActualPower]:
        # TODO feels like there is some duplicated code here which could be refactored

        # Get the window
        start, end = get_window()

        with self._get_session() as session:
            check_user_has_access_to_site(
                session=session,
                email=authdata[EMAIL_KEY],
                site_uuid=site_uuid,
            )

            # read actual generations
            values = get_pv_generation_by_sites(
                session=session,
                site_uuids=[site_uuid],
                start_utc=start,
                end_utc=end,
            )

        # convert from GenerationSQL to PredictedPower
        out = [
            models.ActualPower(
                PowerKW=int(value.generation_power_kw)
                if value.generation_power_kw >= 0
                else 0,  # Set negative values of PowerKW up to 0
                Time=value.start_utc.replace(tzinfo=dt.UTC),
            )
            for value in values
        ]

        return out

    @override
    async def post_site_generation(
        self,
        site_uuid: UUID,
        generation: list[models.ActualPower],
        authdata: dict[str, str],
    ) -> None:
        with self._get_session() as session:
            check_user_has_access_to_site(
                session=session,
                email=authdata[EMAIL_KEY],
                site_uuid=site_uuid,
            )

            generations = []
            for pv_actual_value in generation:
                generations.append(
                    {
                        "start_utc": pv_actual_value.Time,
                        "power_kw": pv_actual_value.PowerKW,
                        "site_uuid": site_uuid,
                    },
                )

            generation_values_df = pd.DataFrame(generations)
            capacity_factor = float(os.getenv("ERROR_GENERATION_CAPACITY_FACTOR", 1.1))
            site = get_site_by_uuid(session=session, site_uuid=str(site_uuid))
            site_capacity_kw = site.capacity_kw
            exceeded_capacity = generation_values_df[
                generation_values_df["power_kw"] > site_capacity_kw * capacity_factor
            ]
            if len(exceeded_capacity) > 0:
                # alert Sentry and return 422 validation error
                sentry_sdk.capture_message(
                    f"Error processing generation values. "
                    f"One (or more) values are larger than {capacity_factor} "
                    f"times the site capacity of {site_capacity_kw} kWp. "
                    # f"User: {auth['https://openclimatefix.org/email']}"
                    f"Site: {site_uuid}",
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Error processing generation values. "
                        f"One (or more) values are larger than {capacity_factor} "
                        f"times the site capacity of {site_capacity_kw} kWp. "
                        "Please adjust this generation value, the site capacity, "
                        "or contact quartz.support@openclimatefix.org."
                    ),
                )

            insert_generation_values(session, generation_values_df)
            session.commit()

    @override
    async def get_substations(
        self,
        auth: AuthDependency,
    ) -> list[models.Substation]:
        raise NotImplementedError("QuartzDB backend does not support substations")

    @override
    async def get_substation_forecast(
        self,
        substation_uuid: UUID,
        auth: AuthDependency,
    ) -> list[models.PredictedPower]:
        raise NotImplementedError("QuartzDB backend does not support substations")

    @override
    async def get_substation(
        self,
        location_uuid: UUID,
        auth: AuthDependency,
    ) -> models.SubstationProperties:
        raise NotImplementedError("QuartzDB backend does not support substations")

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
