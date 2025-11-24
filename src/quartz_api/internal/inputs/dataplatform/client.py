"""A data platform implementation that conforms to the DatabaseInterface."""

import asyncio
from fastapi import HTTPException
from typing import override

from dp_sdk.ocf import dp

from quartz_api import internal
from quartz_api.internal.models import ForecastHorizon
from quartz_api.internal.service.auth import EMAIL_KEY

from ..utils import get_window


class Client(internal.DatabaseInterface):
    """Defines a data platform interface that conforms to the DatabaseInterface."""

    c: dp.DataPlatformDataServiceStub

    @classmethod
    def new(cls, dp_client: dp.DataPlatformDataServiceStub) -> "Client":
        """Class method to create a new Data Platform client."""
        instance = cls()
        instance.c = dp_client
        return instance

    @override
    async def get_predicted_solar_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[internal.PredictedPower]:
        """Overrides parent function."""
        values = await self._get_predicted_power_production_for_location(
            location,
            dp.EnergySource.SOLAR,
            forecast_horizon,
            forecast_horizon_minutes,
            smooth_flag,
        )
        return values

    @override
    async def get_predicted_wind_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[internal.PredictedPower]:
        """Overrides parent function."""
        values = self._get_predicted_power_production_for_location(
            location,
            dp.EnergySource.WIND,
            forecast_horizon,
            forecast_horizon_minutes,
            smooth_flag,
        )
        return values

    @override
    async def get_actual_solar_power_production_for_location(
        self,
        location: str,
    ) -> list[internal.ActualPower]:
        """Overrides parent function."""
        return self._get_actual_power_production_for_location(
            location,
            dp.EnergySource.SOLAR,
        )

    @override
    async def get_actual_wind_power_production_for_location(
        self,
        location: str,
    ) -> list[internal.ActualPower]:
        """Overrides parent function."""
        return self._get_actual_power_production_for_location(
            location,
            dp.EnergySource.WIND,
        )

    @override
    async def get_wind_regions(self) -> list[str]:
        """Overrides parent method."""
        req = dp.ListLocationsRequest(
            energy_source_filter=dp.EnergySource.WIND,
            location_type_filter=dp.LocationType.SITE,
        )
        resp = await self.c.list_locations(req)
        return [loc.location_uuid for loc in resp.locations]

    @override
    async def get_solar_regions(self) -> list[str]:
        """Overrides parent method."""
        req = dp.ListLocationsRequest(
            energy_source_filter=dp.EnergySource.SOLAR,
            location_type_filter=dp.LocationType.SITE,
        )
        resp = await self.c.list_locations(req)
        return [loc.location_uuid for loc in resp.locations]

    @override
    async def get_sites(self, authdata: dict[str, str]) -> list[internal.Site]:
        """Overrides parent method."""
        req = dp.ListLocationsRequest(
            energy_source_filter=dp.EnergySource.SOLAR,
            location_type_filter=dp.LocationType.SITE,
            user_oauth_id_filter=authdata["sub"],
        )
        resp = await self.c.list_locations(req)
        return [
            internal.Site(
                site_uuid=loc.location_uuid,
                client_site_name=loc.location_name,
                orientation=loc.metadata.fields["orientation"].number_value
                if "orientation" in loc.metadata.fields
                else None,
                tilt=loc.metadata.fields["tilt"].number_value
                if "tilt" in loc.metadata.fields
                else None,
                capacity_kw=loc.effective_capacity_watts // 1000.0,
                latitude=loc.latlng.latitude,
                longitude=loc.latlng.longitude,
            )
            for loc in resp.locations
        ]

    @override
    async def put_site(
        self,
        site_uuid: str,
        site_properties: internal.SiteProperties,
        authdata: dict[str, str],
    ) -> internal.Site:
        """Overrides parent method."""
        raise NotImplementedError("Data Platform client doesn't yet support site writing.")

    @override
    async def get_site_forecast(
        self,
        site_uuid: str,
        authdata: dict[str, str],
    ) -> list[internal.PredictedPower]:
        """Overrides parent method."""
        start, end = get_window()
        forecast = await self._get_predicted_power_production_for_location(
            site_uuid,
            dp.EnergySource.SOLAR,
            authdata["sub"],
        )
        return forecast

    @override
    async def get_site_generation(
        self,
        site_uuid: str,
        authdata: dict[str, str],
    ) -> list[internal.ActualPower]:
        """Overrides parent method."""
        start, end = get_window()
        generation = await self._get_actual_power_production_for_location(
            site_uuid,
            dp.EnergySource.SOLAR,
            authdata["sub"],
        )
        return generation

    @override
    async def post_site_generation(
        self,
        site_uuid: str,
        generation: list[internal.ActualPower],
        authdata: dict[str, str],
    ) -> None:
        """Overrides parent method."""
        raise NotImplementedError("Data Platform client doesn't yet support site writing.")

    @override
    async def save_api_call_to_db(self, url: str, authdata: dict[str, str]):
        """Overrides parent method."""
        raise NotImplementedError("Data Platform client doesn't yet support API call logging.")

    async def _get_actual_power_production_for_location(
        self,
        location: str,
        energy_source: dp.EnergySource,
        oauth_id: str,
    ) -> list[internal.ActualPower]:
        """Local function to retrieve actual values regardless of energy type."""
        await self._check_user_access(
            location,
            energy_source,
            dp.LocationType.SITE,
            oauth_id,
        )

        start, end = get_window()
        req = dp.GetObservationsAsTimeseriesRequest(
            location_uuid=location,
            observer_name="TODO",
            energy_source=energy_source,
            time_window=dp.TimeWindow(
                start_timestamp_utc=start,
                end_timestamp_utc=end,
            ),
        )
        resp = await self.c.get_observations_as_timeseries(req)
        out: list[internal.ActualPower] = [
            internal.ActualPower(
                Time=value.timestamp_utc,
                PowerKW=int(value.effective_capacity_watts * value.value_fraction / 1000.0),
            )
            for value in resp.values
        ]

        return out

    async def _get_predicted_power_production_for_location(
        self,
        location: str,
        energy_source: dp.EnergySource,
        oauth_id: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[internal.PredictedPower]:
        """Local function to retrieve predicted values regardless of energy type."""
        _ = await self._check_user_access(
            location,
            energy_source,
            dp.LocationType.SITE,
            oauth_id,
        )

        start, end = get_window()

        if forecast_horizon == ForecastHorizon.latest:
            forecast_horizon_minutes = 0
        if forecast_horizon_minutes is None:
            forecast_horizon_minutes = 0

        req = dp.GetForecastAsTimeseriesRequest(
            location_uuid=location,
            energy_source=energy_source,
            horizon_mins=forecast_horizon_minutes,
            time_window=dp.TimeWindow(
                start_timestamp_utc=start,
                end_timestamp_utc=end,
            ),
            forecaster=dp.Forecaster(
                forecaster_name="TODO",
                forecaster_version="TODO",
            ),
        )
        resp = await self.c.get_forecast_as_timeseries(req)

        out: list[internal.PredictedPower] = [
            internal.PredictedPower(
                Time=value.target_timestamp_utc,
                PowerKW=int(value.effective_capacity_watts * value.p50_value_fraction / 1000.0),
                CreatedTime=value.created_timestamp_utc,
            )
            for value in resp.values
        ]
        return out

    async def _check_user_access(
        self,
        location: str,
        energy_source: dp.EnergySource,
        location_type: dp.LocationType,
        oauth_id: str,
    ) -> bool:
        """Check if a user has access to a given location."""
        req = dp.ListLocationsRequest(
            location_uuids_filter=[location],
            energy_source_filter=energy_source,
            location_type_filter=location_type,
            user_oauth_id_filter=oauth_id,
        )
        resp = await self.c.list_locations(req)
        if len(resp.locations) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No location found for UUID {location} and OAuth ID {oauth_id}",
            )
        return True
