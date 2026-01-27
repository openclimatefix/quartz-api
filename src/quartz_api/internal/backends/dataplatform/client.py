"""A data platform implementation that conforms to the DatabaseInterface."""

import datetime as dt
import logging
from struct import Struct
from uuid import UUID

from dp_sdk.ocf import dp
from fastapi import HTTPException
from typing_extensions import override

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import get_oauth_id_from_sub

log = logging.getLogger("dataplatform.client")


class Client(models.DatabaseInterface):
    """Defines a data platform interface that conforms to the DatabaseInterface."""

    dp_client: dp.DataPlatformDataServiceStub

    @classmethod
    def from_dp(cls, dp_client: dp.DataPlatformDataServiceStub) -> "Client":
        """Class method to create a new Data Platform client."""
        instance = cls()
        instance.dp_client = dp_client
        return instance

    @override
    async def get_predicted_solar_power_production_for_location(
        self,
        location: UUID,
        start_datetime: dt.datetime,
        end_datetime: dt.datetime,
        forecast_horizon: models.ForecastHorizon = models.ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
        forecaster_name: str | None = None,
        created_before_datetime: dt.datetime | None = None,
    ) -> list[models.PredictedPower]:
        values = await self._get_predicted_power_production_for_location(
            location_uuid=location,
            energy_source=dp.EnergySource.SOLAR,
            forecast_horizon=forecast_horizon,
            forecast_horizon_minutes=forecast_horizon_minutes,
            smooth_flag=smooth_flag,
            oauth_id=None,
            forecaster_name=forecaster_name,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            created_before_datetime=created_before_datetime,
        )
        return values

    @override
    async def get_predicted_wind_power_production_for_location(
        self,
        location: UUID,
        forecast_horizon: models.ForecastHorizon = models.ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[models.PredictedPower]:
        values = await self._get_predicted_power_production_for_location(
            location_uuid=location,
            energy_source=dp.EnergySource.WIND,
            forecast_horizon=forecast_horizon,
            forecast_horizon_minutes=forecast_horizon_minutes,
            smooth_flag=smooth_flag,
            oauth_id=None,
        )
        return values

    @override
    async def get_actual_solar_power_production_for_location(
        self,
        location: UUID,
        start_datetime: dt.datetime,
        end_datetime: dt.datetime ,
        observer_name: str | None = None,
    ) -> list[models.ActualPower]:
        values = await self._get_actual_power_production_for_location(
            location,
            dp.EnergySource.SOLAR,
            oauth_id=None,
            observer_name=observer_name,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        return values

    @override
    async def get_actual_wind_power_production_for_location(
        self,
        location: UUID,
    ) -> list[models.ActualPower]:
        values = await self._get_actual_power_production_for_location(
            location,
            dp.EnergySource.WIND,
            oauth_id=None,
        )
        return values

    @override
    async def get_wind_regions(self) -> list[str]:
        req = dp.ListLocationsRequest(
            energy_source_filter=dp.EnergySource.WIND,
            location_type_filter=dp.LocationType.STATE,
        )
        resp = await self.dp_client.list_locations(req)
        return [loc.location_uuid for loc in resp.locations]

    @override
    async def get_solar_regions(self, type: str | None = None) -> list[models.Region]:


        location_type_filter = dp.LocationType.STATE
        if type == "nation":
            location_type_filter = dp.LocationType.NATION
        elif type == "gsp":
            location_type_filter = dp.LocationType.GSP

        req = dp.ListLocationsRequest(
            energy_source_filter=dp.EnergySource.SOLAR,
            location_type_filter=location_type_filter,
        )
        resp = await self.dp_client.list_locations(req)

        regions = []
        for loc in resp.locations:
                region = models.Region(
                    region_name=loc.location_name,
                    region_metadata={
                        "location_uuid": loc.location_uuid,
                        "effective_capacity_watts": loc.effective_capacity_watts,
                        **dict(struct_to_dict(loc.metadata)),

                    },
                )
                regions.append(region)
        return regions

    @override
    async def get_sites(self, authdata: dict[str, str]) -> list[models.Site]:
        req = dp.ListLocationsRequest(
            energy_source_filter=dp.EnergySource.SOLAR,
            location_type_filter=dp.LocationType.SITE,
            user_oauth_id_filter=authdata["sub"],
        )
        resp = await self.dp_client.list_locations(req)
        return [
            models.Site(
                site_uuid=loc.location_uuid,
                client_site_name=loc.location_name,
                orientation=loc.metadata.fields["orientation"].number_value
                if "orientation" in loc.metadata.fields
                else None,
                tilt=loc.metadata.fields["tilt"].number_value
                if "tilt" in loc.metadata.fields
                else None,
                capacity_kW=loc.effective_capacity_watts // 1000.0,
                latitude=loc.latlng.latitude,
                longitude=loc.latlng.longitude,
            )
            for loc in resp.locations
        ]

    @override
    async def put_site(
        self,
        site_uuid: UUID,
        site_properties: models.SiteProperties,
        authdata: dict[str, str],
    ) -> models.Site:
        raise NotImplementedError("Data Platform client doesn't yet support site writing.")

    @override
    async def get_site_forecast(
        self,
        site_uuid: UUID,
        start_datetime: dt.datetime,
        end_datetime: dt.datetime,
        authdata: dict[str, str],
    ) -> list[models.PredictedPower]:

        forecast = await self._get_predicted_power_production_for_location(
            site_uuid,
            dp.EnergySource.SOLAR,
            authdata["sub"],
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        return forecast

    @override
    async def get_site_generation(
        self,
        site_uuid: UUID,
        start_datetime: dt.datetime,
        end_datetime: dt.datetime,
        authdata: dict[str, str],
    ) -> list[models.ActualPower]:
        generation = await self._get_actual_power_production_for_location(
            site_uuid,
            dp.EnergySource.SOLAR,
            authdata["sub"],
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        return generation

    @override
    async def post_site_generation(
        self,
        site_uuid: UUID,
        generation: list[models.ActualPower],
        authdata: dict[str, str],
    ) -> None:
        raise NotImplementedError("Data Platform client doesn't yet support site writing.")

    @override
    async def save_api_call_to_db(self, url: str, authdata: dict[str, str]) -> None:
        log.warning("Data Platform client does not support logging API calls to DB.")
        pass

    @override
    async def get_substations(
        self,
        authdata: dict[str, str],
    ) -> list[models.Substation]:
        oauth_id = get_oauth_id_from_sub(authdata["sub"]) if "sub" in authdata else None
        req = dp.ListLocationsRequest(
            energy_source_filter=dp.EnergySource.SOLAR,
            location_type_filter=dp.LocationType.PRIMARY_SUBSTATION,
            user_oauth_id_filter=oauth_id,
        )
        resp = await self.dp_client.list_locations(req)

        return [
            models.Substation(
                substation_uuid=loc.location_uuid,
                substation_name=loc.location_name,
                substation_type="primary"
                if loc.location_type == dp.LocationType.PRIMARY_SUBSTATION
                else "secondary",
                capacity_kW=loc.effective_capacity_watts // 1000.0,
                latitude=loc.latlng.latitude,
                longitude=loc.latlng.longitude,
                metadata = struct_to_dict(loc.metadata),
            )
            for loc in resp.locations
        ]

    @override
    async def get_substation_forecast(
        self,
        location_uuid: UUID,
        authdata: dict[str, str],
        start_datetime: dt.datetime,
        end_datetime: dt.datetime,
        traceid: str = "unknown",
    ) -> list[models.PredictedPower]:
        # Get the substation
        oauth_id = get_oauth_id_from_sub(authdata["sub"]) if "sub" in authdata else None
        # Get the substation to ensure user has access and it exists
        req = dp.ListLocationsRequest(
            location_uuids_filter=[str(location_uuid)],
            energy_source_filter=dp.EnergySource.SOLAR,
            location_type_filter=dp.LocationType.PRIMARY_SUBSTATION,
            user_oauth_id_filter=oauth_id,
        )
        resp = await self.dp_client.list_locations(req)
        if len(resp.locations) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No substation found for UUID '{location_uuid}'",
            )
        substation = resp.locations[0]

        # Get the GSP that the substation belongs to
        req = dp.ListLocationsRequest(
            enclosed_location_uuid_filter=[str(location_uuid)],
            location_type_filter=dp.LocationType.GSP,
            user_oauth_id_filter=oauth_id,
        )
        gsps = await self.dp_client.list_locations(req)
        if len(gsps.locations) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No GSP found for substation UUID '{location_uuid}'",
            )
        gsp = gsps.locations[0]
        forecast = await self._get_predicted_power_production_for_location(
            location_uuid=gsp.location_uuid,
            energy_source=dp.EnergySource.SOLAR,
            oauth_id=oauth_id,
            start_datetime=start_datetime,
            end_datetime=start_datetime,
        )

        # Scale the forecast to the substation capacity
        scale_factor: float = substation.effective_capacity_watts / gsp.effective_capacity_watts
        for value in forecast:
            value.power_kW = value.power_kW * scale_factor

        log.debug(
            "gsp=%s, substation=%s, scalefactor=%s, scaling GSP to substation",
            gsp.location_uuid,
            substation.location_uuid,
            scale_factor,
        )

        return forecast

    @override
    async def get_substation(
        self,
        location_uuid: UUID,
        authdata: dict[str, str],
    ) -> models.SubstationProperties:
        oauth_id = get_oauth_id_from_sub(authdata["sub"]) if "sub" in authdata else None
        req = dp.ListLocationsRequest(
            location_uuids_filter=[str(location_uuid)],
            energy_source_filter=dp.EnergySource.SOLAR,
            user_oauth_id_filter=oauth_id,
        )
        resp = await self.dp_client.list_locations(req)
        if len(resp.locations) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No substation found for UUID '{location_uuid}'",
            )
        loc = resp.locations[0]

        return models.SubstationProperties(
            substation_name=loc.location_name,
            substation_type="primary",
            capacity_kW=loc.effective_capacity_watts // 1000.0,
            latitude=loc.latlng.latitude,
            longitude=loc.latlng.longitude,
        )

    async def _get_actual_power_production_for_location(
        self,
        location_uuid: UUID,
        energy_source: dp.EnergySource,
        oauth_id: str | None,
        start_datetime: dt.datetime,
        end_datetime: dt.datetime,
        observer_name: str = "ruvnl",
    ) -> list[models.ActualPower]:
        """Local function to retrieve actual values regardless of energy type."""
        if oauth_id is not None:
            await self._check_user_access(
                location_uuid,
                energy_source,
                dp.LocationType.SITE,
                oauth_id,
            )

        req = dp.GetObservationsAsTimeseriesRequest(
            location_uuid=str(location_uuid),
            observer_name=observer_name,
            energy_source=energy_source,
            time_window=dp.TimeWindow(
                start_timestamp_utc=start_datetime,
                end_timestamp_utc=end_datetime,
            ),
        )
        resp = await self.dp_client.get_observations_as_timeseries(req)
        out: list[models.ActualPower] = [
            models.ActualPower(
                Time=value.timestamp_utc,
                PowerKW=int(value.effective_capacity_watts * value.value_fraction / 1000.0),
                location_uuid=str(location_uuid),
            )
            for value in resp.values
        ]

        return out

    async def _get_predicted_power_production_for_location(
        self,
        location_uuid: UUID,
        energy_source: dp.EnergySource,
        oauth_id: str | None,
        start_datetime: dt.datetime,
        end_datetime: dt.datetime,
        forecast_horizon: models.ForecastHorizon = models.ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,  # noqa: ARG002
        forecaster_name: str | None = None,
        created_before_datetime: dt.datetime | None = None,
    ) -> list[models.PredictedPower]:
        """Local function to retrieve predicted values regardless of energy type."""
        if oauth_id is not None:
            _ = await self._check_user_access(
                location_uuid,
                energy_source,
                dp.LocationType.SITE,
                oauth_id,
            )

        if forecast_horizon == models.ForecastHorizon.latest or forecast_horizon_minutes is None:
            forecast_horizon_minutes = 0
        elif forecast_horizon == models.ForecastHorizon.day_ahead:
            # The intra-day forecast caps out at 8 hours horizon, so anything greater than that is
            # assumed to be day-ahead. It doesn't seem like it's as simple as just using 24 hours,
            # from my asking around at least
            forecast_horizon_minutes = 9 * 60

        if forecaster_name is None:
            # Use the forecaster that produced the most recent forecast for the location by default,
            # taking into account the desired horizon.
            # * At some point, we may want to allow the user to specify a particular forecaster.
            req = dp.GetLatestForecastsRequest(
                location_uuid=str(location_uuid),
                energy_source=energy_source,
                pivot_timestamp_utc=start_datetime - dt.timedelta(minutes=forecast_horizon_minutes),
            )
            resp = await self.dp_client.get_latest_forecasts(req)
            if len(resp.forecasts) == 0:
                return []
            resp.forecasts.sort(
                key=lambda f: f.created_timestamp_utc,
                reverse=True,
            )
            forecaster = resp.forecasts[0].forecaster
        else:
            req = dp.ListForecastersRequest(forecaster_names_filter=[forecaster_name],
                                      latest_versions_only=True)
            resp = await self.dp_client.list_forecasters(req)
            forecaster = resp.forecasters[0]

        req = dp.GetForecastAsTimeseriesRequest(
            location_uuid=str(location_uuid),
            energy_source=energy_source,
            horizon_mins=forecast_horizon_minutes,
            time_window=dp.TimeWindow(
                start_timestamp_utc=start_datetime,
                end_timestamp_utc=end_datetime,
            ),
            forecaster=forecaster,
            pivot_timestamp_utc=created_before_datetime,
        )
        resp = await self.dp_client.get_forecast_as_timeseries(req)

        out: list[models.PredictedPower] = [
            models.PredictedPower(
                time=value.target_timestamp_utc,
                power_kW=int(value.effective_capacity_watts * value.p50_value_fraction / 1000.0),
                created_time=value.created_timestamp_utc,
                plevel_kW={
                    "p10": int(value.effective_capacity_watts \
                               * value.other_statistics_fractions["p10"] / 1000.0),
                    "p90": int(value.effective_capacity_watts \
                               * value.other_statistics_fractions["p90"] / 1000.0),
                } if "p10" in value.other_statistics_fractions
                and "p90" in value.other_statistics_fractions else {},
                forecaster_name=forecaster.forecaster_name,
                forecaster_version=forecaster.forecaster_version,
                initialization_timestamp_utc=value.initialization_timestamp_utc,
            )
            for value in resp.values
        ]
        return out

    async def _check_user_access(
        self,
        location_uuid: UUID,
        energy_source: dp.EnergySource,
        location_type: dp.LocationType,
        oauth_id: str,
    ) -> bool:
        """Check if a user has access to a given location."""
        req = dp.ListLocationsRequest(
            location_uuids_filter=[location_uuid],
            energy_source_filter=energy_source,
            location_type_filter=location_type,
            user_oauth_id_filter=oauth_id,
        )
        resp = await self.dp_client.list_locations(req)
        if len(resp.locations) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No location found for UUID {location_uuid} and OAuth ID {oauth_id}",
            )
        return True


    @override
    async def get_forecast_for_multiple_locations_one_timestamp(
        self,
        location_uuids: dict[str, int],
        authdata: dict[str, str],
        datetime_utc: dt.datetime,
        forecaster_name: str = "blend",
        forecaster_version:str | None = None,

    ) -> list[models.OneDatetimeManyForecastValues]:
        """Get a forecast for multiple sites.

        Args:
            location_uuids: A list of location UUIDs.
            authdata: Authentication data for the user.
            datetime_utc: The datetime for the prediction window
            forecaster_name: The name of the forecasting model to use. Default is blend.
            forecaster_version: The version of the forecasting model to use. Default is None.

        Returns:
            A list of OneDatetimeManyForecastValues objects.
        """
        if forecaster_version is None:
            # get forecasters"
            req = dp.ListForecastersRequest(forecaster_names_filter=[forecaster_name],
                                          latest_versions_only=True)
            resp = await self.dp_client.list_forecasters(req)
            forecaster = resp.forecasters[0]
        else:
            forecaster = dp.Forecaster(
                forecaster_name=forecaster_name,
                forecaster_version=forecaster_version,
            )

        req = dp.GetForecastAtTimestampRequest(
            location_uuids=location_uuids,
            energy_source=dp.EnergySource.SOLAR,
            timestamp_utc=datetime_utc,
            forecaster=forecaster,
        )
        resp = await self.dp_client.get_forecast_at_timestamp(req)


        forecasts_one_timestamp = models.OneDatetimeManyForecastValues(
            datetime_utc=resp.timestamp_utc,
            forecast_values_kW={
                forecast.location_uuid: round(
                    forecast.value_fraction * forecast.effective_capacity_watts / 10**3, 2)
                for forecast in resp.values
            })

        # sort by dictionary by keys
        forecasts_one_timestamp.forecast_values_kW =\
            dict(sorted(forecasts_one_timestamp.forecast_values_kW.items()))


        return forecasts_one_timestamp

    @override
    async def get_latest_forecast(
        self,
        authdata: dict[str, str],
        location_uuid: UUID,
        forecaster_name: str | None = None,
    ) -> models.Forecast:
        """Get the latest forecast for a site."""
        # get forecasters
        request = dp.GetLatestForecastsRequest(
            location_uuid=str(location_uuid),
            energy_source=dp.EnergySource.SOLAR,
        )
        response = await self.dp_client.get_latest_forecasts(request)

        # reduce to forecaster_name
        if forecaster_name is not None:
            response.forecasts = [
                f for f in response.forecasts
                if f.forecaster.forecaster_name == forecaster_name
            ]

        # shouldnt need this but just incase
        response.forecasts.sort(
            key=lambda f: f.created_timestamp_utc,
            reverse=True,
        )

        return models.Forecast(
            created_time=response.forecasts[0].created_timestamp_utc,
            name=response.forecasts[0].forecaster.forecaster_name,
            version=response.forecasts[0].forecaster.forecaster_version,
        )


def struct_to_dict(values:Struct) -> dict:
    """Converts a Struct to a dictionary."""
    d = values.to_dict()

    # change any number_values to float
    for key, value in d.items():
        if isinstance(value, dict):
            if "numberValue" in value:
                d[key] = float(value["numberValue"])
            if "stringValue" in value:
                d[key] = str(value["stringValue"])

    return d

