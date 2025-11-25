"""Quartz DB client that conforms to the DatabaseInterface."""
import os
import datetime as dt
import pandas as pd
import logging
from typing import Optional
from fastapi import HTTPException
from uuid import UUID
import sentry_sdk

from pvsite_datamodel import DatabaseConnection
from pvsite_datamodel.read import (
    get_sites_by_country,
    get_latest_forecast_values_by_site,
    get_pv_generation_by_sites,
    get_user_by_email,
    get_sites_from_user,
    get_site_by_uuid,
)
from pvsite_datamodel.write.generation import insert_generation_values
from pvsite_datamodel.sqlmodels import LocationAssetType, ForecastValueSQL
from pvsite_datamodel.write.database import save_api_call_to_db
from pvsite_datamodel.write.user_and_site import edit_site
from pvsite_datamodel.pydantic_models import PVSiteEditMetadata
from sqlalchemy.orm import Session

from quartz_api import internal
from quartz_api.internal.inputs.utils import get_window
from quartz_api.internal.inputs.quartzdb.smooth import smooth_forecast
from quartz_api.internal.models import ForecastHorizon

log = logging.getLogger(__name__)


class Client(internal.DatabaseInterface):
    """Defines Quartz DB client that conforms to the DatabaseInterface."""

    session: Session = None

    def __init__(self, database_url: str) -> None:
        """Initialize the client with a SQLAlchemy database connection and session."""

        self.connection = DatabaseConnection(url=database_url, echo=False)

    def _get_session(self):
        """Allows for overriding the default session (useful for testing)"""
        if self.session is None:
            return self.connection.get_session()
        else:
            return self.session

    def save_api_call_to_db(self, url: str, email=""):
        """Saves an API call to the database."""
        with self._get_session() as session:
            # save the API call
            log.info(f"Saving API call ({url=}) to database")
            user = get_user_by_email(session, email)
            save_api_call_to_db(url=url, session=session, user=user)

    def get_predicted_power_production_for_location(
        self,
        location: str,
        asset_type: LocationAssetType,
        ml_model_name: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: Optional[int] = None,
        smooth_flag: bool = True,
    ) -> list[internal.PredictedPower]:
        """Gets the predicted power production for a location.

        Args:
            location: the location to get the predicted power production for
            asset_type: The type of asset to get the forecast for
            ml_model_name: The name of the model to get the forecast from
            forecast_horizon: The time horizon to get the data for. Can be latest or day ahead
            forecast_horizon_minutes: The number of minutes to get the forecast for. forecast_horizon must be 'horizon'
            smooth_flag: Flag to smooth the forecast
        """

        # Get the window
        start, end = get_window()

        # get house ahead forecast
        if forecast_horizon == ForecastHorizon.day_ahead:
            day_ahead_hours = 9
            day_ahead_timezone_delta_hours = 5.5
            forecast_horizon_minutes = None
        elif forecast_horizon == ForecastHorizon.horizon:
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
            forecast_values: [ForecastValueSQL] = values[site.location_uuid]

        # convert ForecastValueSQL to PredictedPower
        values = [
            internal.PredictedPower(
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
            values = smooth_forecast(values)

        return values

    def get_generation_for_location(
        self,
        location: str,
        asset_type: LocationAssetType,
    ) -> [internal.PredictedPower]:
        """Gets the predicted power production for a location."""

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
                session=session, site_uuids=[site.location_uuid], start_utc=start, end_utc=end
            )

        # convert from GenerationSQL to ActualPower
        values = [
            internal.ActualPower(
                PowerKW=int(value.generation_power_kw)
                if value.generation_power_kw >= 0
                else 0,  # Set negative values of PowerKW up to 0
                Time=value.start_utc.replace(tzinfo=dt.UTC),
            )
            for value in values
        ]

        return values

    def get_predicted_solar_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: Optional[int] = None,
        smooth_flag: bool = True,
    ) -> [internal.PredictedPower]:
        """
        Gets the predicted solar power production for a location.

        Args:
            location: The location to get the predicted solar power production for.
            forecast_horizon: The time horizon to get the data for. Can be latest or day ahead
            forecast_horizon_minutes: The number of minutes to get the forecast for. forecast_horizon must be 'horizon'
            smooth_flag: Flag to smooth the forecast
        """

        # set this to be hard coded for now
        model_name = "pvnet_india"

        return self.get_predicted_power_production_for_location(
            location=location,
            asset_type=LocationAssetType.pv,
            forecast_horizon=forecast_horizon,
            forecast_horizon_minutes=forecast_horizon_minutes,
            smooth_flag=smooth_flag,
            ml_model_name=model_name,
        )

    def get_predicted_wind_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: Optional[int] = None,
        smooth_flag: bool = True,
    ) -> list[internal.PredictedPower]:
        """
        Gets the predicted wind power production for a location.

        Args:
            location: The location to get the predicted wind power production for.
            forecast_horizon: The time horizon to get the data for. Can be latest or day ahead
            forecast_horizon_minutes: The number of minutes to get the forecast for. forecast_horizon must be 'horizon'
            smooth_flag: Flag to smooth the forecast
        """

        # set this to be hard coded for now
        model_name = "windnet_india_adjust"

        return self.get_predicted_power_production_for_location(
            location=location,
            asset_type=LocationAssetType.wind,
            forecast_horizon=forecast_horizon,
            forecast_horizon_minutes=forecast_horizon_minutes,
            smooth_flag=smooth_flag,
            ml_model_name=model_name,
        )

    def get_actual_solar_power_production_for_location(
        self, location: str
    ) -> list[internal.PredictedPower]:
        """Gets the actual solar power production for a location."""

        return self.get_generation_for_location(location=location, asset_type=LocationAssetType.pv)

    def get_actual_wind_power_production_for_location(
        self, location: str
    ) -> list[internal.PredictedPower]:
        """Gets the actual wind power production for a location."""

        return self.get_generation_for_location(location=location, asset_type=LocationAssetType.wind)

    def get_wind_regions(self) -> list[str]:
        """Gets the valid wind regions."""
        return ["ruvnl"]

    def get_solar_regions(self) -> list[str]:
        """Gets the valid solar regions."""
        return ["ruvnl"]

    def get_sites(self, email: str) -> list[internal.Site]:
        """Get a list of sites"""

        # get sites uuids from user
        with self._get_session() as session:
            user = get_user_by_email(session, email)
            sites_sql = get_sites_from_user(session, user=user)

            sites = []
            for site_sql in sites_sql:
                site = internal.Site(
                    site_uuid=str(site_sql.location_uuid),
                    client_site_name=site_sql.client_location_name,
                    orientation=site_sql.orientation,
                    tilt=site_sql.tilt,
                    capacity_kw=site_sql.capacity_kw,
                    latitude=site_sql.latitude,
                    longitude=site_sql.longitude,
                )
                sites.append(site)

            return sites

    def put_site(
        self, site_uuid: str, site_properties: internal.SiteProperties, email: str
    ) -> internal.Site:
        """update site information for a single site."""

        # get sites uuids from user
        with self._get_session() as session:
            user = get_user_by_email(session, email)
            site = get_site_by_uuid(session, site_uuid)
            check_user_has_access_to_site(session, email, site.location_uuid)

            site_dict = site_properties.model_dump(exclude_unset=True, exclude_none=False)

            site_info = PVSiteEditMetadata(**site_dict)

            site, _ = edit_site(
                session=session,
                site_uuid=site_uuid,
                site_info=site_info,
                user_uuid=user.user_uuid,
            )

            return site

    def get_site_forecast(self, site_uuid: str, email: str) -> list[internal.PredictedPower]:
        """Get a forecast for a site, this is for a solar site"""

        # TODO feels like there is some duplicated code here which could be refactored

        # hard coded model name
        ml_model_name = "pvnet_ad_sites"

        # Get the window
        start, _ = get_window()

        with self._get_session() as session:
            check_user_has_access_to_site(session=session, email=email, site_uuid=site_uuid)

            # get site and the get the ml model name
            site = get_site_by_uuid(session=session, site_uuid=site_uuid)
            if site.ml_model is not None:
                ml_model_name = site.ml_model.name
            log.info(f"Using ml model {ml_model_name}")

            if isinstance(site_uuid, str):
                site_uuid = UUID(site_uuid)

            values = get_latest_forecast_values_by_site(
                session, site_uuids=[site_uuid], start_utc=start, model_name=ml_model_name
            )
            forecast_values: [ForecastValueSQL] = values[site_uuid]

            # convert ForecastValueSQL to PredictedPower
        values = [
            internal.PredictedPower(
                PowerKW=int(value.forecast_power_kw)
                if value.forecast_power_kw >= 0
                else 0,  # Set negative values of PowerKW up to 0
                Time=value.start_utc.replace(tzinfo=dt.UTC),
                CreatedTime=value.created_utc.replace(tzinfo=dt.UTC),
            )
            for value in forecast_values
        ]

        return values

    def get_site_generation(self, site_uuid: str, email: str) -> list[internal.ActualPower]:
        """Get the generation for a site, this is for a solar site"""

        # TODO feels like there is some duplicated code here which could be refactored

        # Get the window
        start, end = get_window()

        with self._get_session() as session:
            check_user_has_access_to_site(session=session, email=email, site_uuid=site_uuid)

            if isinstance(site_uuid, str):
                site_uuid = UUID(site_uuid)

            # read actual generations
            values = get_pv_generation_by_sites(
                session=session, site_uuids=[site_uuid], start_utc=start, end_utc=end
            )

        # convert from GenerationSQL to PredictedPower
        values = [
            internal.ActualPower(
                PowerKW=int(value.generation_power_kw)
                if value.generation_power_kw >= 0
                else 0,  # Set negative values of PowerKW up to 0
                Time=value.start_utc.replace(tzinfo=dt.UTC),
            )
            for value in values
        ]

        return values

    def post_site_generation(
        self, site_uuid: str, generation: list[internal.ActualPower], email: str
    ):
        """Post generation for a site"""

        with self._get_session() as session:
            check_user_has_access_to_site(session=session, email=email, site_uuid=site_uuid)

            generations = []
            for pv_actual_value in generation:
                generations.append(
                    {
                        "start_utc": pv_actual_value.Time,
                        "power_kw": pv_actual_value.PowerKW,
                        "site_uuid": site_uuid,
                    }
                )

            generation_values_df = pd.DataFrame(generations)
            capacity_factor = float(os.getenv("ERROR_GENERATION_CAPACITY_FACTOR", 1.1))
            site = get_site_by_uuid(session=session, site_uuid=site_uuid)
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
                    f"Site: {site_uuid}"
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


def check_user_has_access_to_site(session: Session, email: str, site_uuid: str):
    """
    Checks if a user has access to a site.
    """

    user = get_user_by_email(session=session, email=email)
    site_uuids = [str(site.location_uuid) for site in user.location_group.locations]
    site_uuid = str(site_uuid)

    if site_uuid not in site_uuids:
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden. User ({email}) "
            f"does not have access to this site {site_uuid}. "
            f"User has access to {site_uuids}",
        )
