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

energy_type_map: dict[models.EnergyType, dp.EnergySource] = {
    models.EnergyType.SOLAR: dp.EnergySource.SOLAR,
    models.EnergyType.WIND: dp.EnergySource.WIND,
}

location_type_map: dict[models.LocationType, dp.LocationType] = {
    models.LocationType.SITE: dp.LocationType.SITE,
    models.LocationType.GSP: dp.LocationType.GSP,
    models.LocationType.REGION: dp.LocationType.STATE,
    models.LocationType.NATION: dp.LocationType.NATION,
    models.LocationType.SUBSTATION: dp.LocationType.PRIMARY_SUBSTATION,
}


class StorageClient(models.StorageInterface):
    """Defines a data platform conneciton that conforms to the StorageInterface."""

    dpc: dp.DataPlatformDataServiceStub

    @classmethod
    def from_dp(cls, dp_client: dp.DataPlatformDataServiceStub) -> "StorageClient":
        """Class method to create a new Data Platform storage client."""
        instance = cls()
        instance.dpc = dp_client
        return instance

    @override
    async def get_predicted_generation(
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
        if forecast_horizon_minutes == 24 * 60:
            # The user is requesting day-ahead.
            # The intra-day forecast caps out at 8 hours horizon, so anything greater than that is
            # assumed to be day-ahead. It doesn't seem like it's as simple as just using 24 hours,
            # from my asking around at least
            forecast_horizon_minutes = 9 * 60

        # Limit the creation time if not set
        if created_cutoff is None:
            created_cutoff = dt.datetime.now(tz=dt.UTC) - \
                dt.timedelta(minutes=forecast_horizon_minutes)

        oauth_id: str | None = get_oauth_id_from_sub(authdata["sub"]) if authdata != {} else None
        req = dp.ListLocationsRequest(
            location_uuids_filter=[str(location_uuid)],
            energy_source_filter=energy_type_map[energy_type],
            location_type_filter=location_type_map[location_type],
            user_oauth_id_filter=oauth_id,
        )
        resp = await self.dpc.list_locations(req)
        if len(resp.locations) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No location found for UUID '{location_uuid}',\
                      {location_type} and {energy_type}",
            )
        location = resp.locations[0]

        if location_type == models.LocationType.SUBSTATION:
            # Get the GSP the substation belongs to
            req = dp.ListLocationsRequest(
                enclosed_location_uuid_filter=[str(location_uuid)],
                location_type_filter=dp.LocationType.GSP,
                user_oauth_id_filter=oauth_id,
            )
            gsps = await self.dpc.list_locations(req)
            if len(gsps.locations) == 0:
                raise HTTPException(
                    status_code=404,
                    detail=f"No GSP found for substation UUID '{location_uuid}'",
                )
            gsp = gsps.locations[0]
            # Spoof the location id so that the forecast is fetched for the GSP instead
            location_uuid = gsp.location_uuid

        if forecaster_name is None:
            # Use the forecaster that produced the most recent forecast for the location by default,
            # taking into account the desired horizon.
            # NOTE: This is a pretty rough-and-ready way of getting the forecaster and should be
            # changed.
            req = dp.GetLatestForecastsRequest(
                location_uuid=str(location_uuid),
                energy_source=energy_type_map[energy_type],
                pivot_timestamp_utc=window_start - dt.timedelta(minutes=forecast_horizon_minutes),
            )
            resp = await self.dpc.get_latest_forecasts(req)
            if len(resp.forecasts) == 0:
                return []
            resp.forecasts.sort(
                key=lambda f: f.created_timestamp_utc,
                reverse=True,
            )
            forecaster = resp.forecasts[0].forecaster
        elif forecaster_version is None:
            req = dp.ListForecastersRequest(
                forecaster_names_filter=[forecaster_name],
                latest_versions_only=True,
            )
            resp = await self.dpc.list_forecasters(req)
            forecaster = resp.forecasters[0]
        else:
            forecaster = dp.Forecaster(
                forecaster_name=forecaster_name,
                forecaster_version=forecaster_version,
            )

        req = dp.GetForecastAsTimeseriesRequest(
            location_uuid=str(location_uuid),
            energy_source=energy_type_map[energy_type],
            horizon_mins=forecast_horizon_minutes,
            time_window=dp.TimeWindow(
                start_timestamp_utc=window_start,
                end_timestamp_utc=window_end,
            ),
            forecaster=forecaster,
            pivot_timestamp_utc=created_cutoff,
        )
        resp = await self.dpc.get_forecast_as_timeseries(req)

        if location_type == models.LocationType.SUBSTATION:
            # Spoof the forecast values so that the capacity and id corresponds to the substation
            for v in resp.values:
                v.location_uuid = location_uuid
                v.effective_capacity_watts = location.effective_capacity_watts

        out: list[models.PredictedGenerationValue] = [
            models.PredictedGenerationValue(
                power_kilowatts=int(
                    v.effective_capacity_watts * v.p50_value_fraction / 1000,
                ),
                valid_timestamp=v.target_timestamp_utc,
                location_uuid=location_uuid,
                capacity_kilowatts=int(v.effective_capacity_watts / 1000),
                created_timestamp=v.created_timestamp_utc,
                init_timestamp=v.initialization_timestamp_utc,
                forecaster_name=forecaster.forecaster_name,
                forecaster_version=forecaster.forecaster_version,
                plevels_kilowatts={
                    "p10": int(
                        v.effective_capacity_watts * v.other_statistics_fractions["p10"] / 1000.0,
                    ),
                    "p90": int(
                        v.effective_capacity_watts * v.other_statistics_fractions["p90"] / 1000.0,
                    ),
                }
                if "p10" in v.other_statistics_fractions and "p90" in v.other_statistics_fractions
                else {},
                metadata=struct_to_dict(v.metadata),
            )
            for v in resp.values
        ]
        return out

    @override
    async def put_predicted_generation(
        self,
        generation_values: list[models.PredictedGenerationValue],
        location_type: models.LocationType,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
    ) -> None:
        raise NotImplementedError("Data platform backend doesn't support forecast input yet.")

    @override
    async def get_actual_generation(
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
        if observer_name is None:
            raise ValueError("Observer must be specified for data platform backend.")

        if authdata != {}:
            _ = await self._check_user_access(
                location_uuid=location_uuid,
                energy_source=energy_type_map[energy_type],
                location_type=location_type_map[location_type],
                oauth_id=get_oauth_id_from_sub(authdata["sub"]),
            )

        req = dp.GetObservationsAsTimeseriesRequest(
            location_uuid=str(location_uuid),
            observer_name=observer_name,
            energy_source=energy_type_map[energy_type],
            time_window=dp.TimeWindow(
                start_timestamp_utc=window_start,
                end_timestamp_utc=window_end,
            ),
        )
        resp = await self.dpc.get_observations_as_timeseries(req)
        out: list[models.ActualGenerationValue] = [
            models.ActualGenerationValue(
                valid_timestamp=value.timestamp_utc,
                power_kilowatts=int(value.effective_capacity_watts * value.value_fraction / 1000.0),
                location_uuid=str(location_uuid),
                capacity_kilowatts=int(value.effective_capacity_watts / 1000.0),
                observer_name=observer_name,
            )
            for value in resp.values
        ]

        return out

    @override
    async def put_actual_generation(
        self,
        generation_values: list[models.ActualGenerationValue],
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
    ) -> None:
        raise NotImplementedError("Data platform backend does not yet support writing generation")

    @override
    async def get_predicted_generation_snapshot(
        self,
        location_uuids: list[UUID],
        snapshot_timestamp_utc: dt.datetime,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
        forecaster_name: str | None = None,
        forecaster_version: str | None = None,
    ) -> list[models.PredictedGenerationValue]:
        if forecaster_version is None:
            req = dp.ListForecastersRequest(
                forecaster_names_filter=[forecaster_name],
                latest_versions_only=True,
            )
            resp = await self.dpc.list_forecasters(req)
            forecaster = resp.forecasters[0]
        else:
            forecaster = dp.Forecaster(
                forecaster_name=forecaster_name,
                forecaster_version=forecaster_version,
            )

        req = dp.GetForecastAtTimestampRequest(
            location_uuids=[str(uuid) for uuid in location_uuids],
            energy_source=energy_type_map[energy_type],
            timestamp_utc=snapshot_timestamp_utc,
            forecaster=forecaster,
        )
        resp = await self.dpc.get_forecast_at_timestamp(req)

        out: list[models.PredictedGenerationValue] = [
            models.PredictedGenerationValue(
                power_kilowatts=v.value_fraction * v.effective_capacity_watts / 1000,
                valid_timestamp=resp.timestamp_utc,
                location_uuid=UUID(v.location_uuid),
                capacity_kilowatts=v.effective_capacity_watts / 1000,
                forecaster_name=forecaster.forecaster_name,
                forecaster_version=forecaster.forecaster_version,
                created_timestamp=v.created_timestamp_utc,
                init_timestamp=v.initialization_timestamp_utc,
                metadata=struct_to_dict(v.metadata),
            )
            for v in resp.values
        ]

        return out

    @override
    async def get_actual_generation_snapshot(
        self,
        location_uuids: list[UUID],
        snapshot_timestamp_utc: dt.datetime,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
        observer_name: str | None = None,
    ) -> list[models.ActualGenerationValue]:
        if observer_name is None:
            raise ValueError("Observer must be specified for data platform backend.")

        req = dp.GetObservationsAtTimestampRequest(
            location_uuids=[str(uuid) for uuid in location_uuids],
            energy_source=energy_type_map[energy_type],
            timestamp_utc=snapshot_timestamp_utc,
            observer_name=observer_name,
        )
        resp = await self.dpc.get_observations_at_timestamp(req)

        out: list[models.ActualGenerationValue] = [
            models.ActualGenerationValue(
                valid_timestamp=resp.timestamp_utc,
                power_kilowatts=round(
                    v.value_fraction * v.effective_capacity_watts / 1000,
                    4,
                ),
                location_uuid=UUID(v.location_uuid),
                capacity_kilowatts=v.effective_capacity_watts / 1000,
                observer_name=observer_name,
            )
            for v in resp.values
        ]

        return out

    @override
    async def get_locations(
        self,
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
        location_uuid: UUID | None = None,
    ) -> list[models.Location]:
        # For the moment, recreate the auth behaviour of the old routes in here.
        # This should be delegated to the scoping on the API endpoints themselves later.
        match energy_type, location_type:
            case models.EnergyType.WIND, models.LocationType.REGION:
                # get_wind_regions had no auth
                oauth_id: str | None = None
            case models.EnergyType.SOLAR, models.LocationType.NATION | models.LocationType.GSP:
                # get_solar_regions had no auth
                oauth_id = None
            case _, models.LocationType.SUBSTATION:
                # get substations had optional auth (?) (temporary while we onboard?)
                oauth_id = get_oauth_id_from_sub(authdata["sub"]) if authdata != {} else None
            case _, models.LocationType.SITE:
                # get_sites had auth
                oauth_id = get_oauth_id_from_sub(authdata["sub"])
            case _:
                oauth_id = get_oauth_id_from_sub(authdata["sub"])

        req = dp.ListLocationsRequest(
            energy_source_filter=energy_type_map[energy_type],
            location_type_filter=location_type_map[location_type],
            user_oauth_id_filter=oauth_id,
            location_uuids_filter=[str(location_uuid)] if location_uuid is not None else [],
        )
        resp = await self.dpc.list_locations(req)
        out: list[models.Location] = [
            models.Location(
                uuid=loc.location_uuid,
                name=loc.location_name,
                capacity_kilowatts=loc.effective_capacity_watts / 1000.0,
                latitude=loc.latlng.latitude,
                longitude=loc.latlng.longitude,
                metadata=struct_to_dict(loc.metadata),
            )
            for loc in resp.locations
        ]
        return out

    @override
    async def put_location(
        self,
        location: models.Location,
        location_type: models.LocationType,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
    ) -> models.Location:
        raise NotImplementedError("Data platform backend doesn't yet support location writing.")

    @override
    async def log_api_call(
        self,
        url: str,
        authdata: dict[str, str],
    ) -> None:
        pass

    async def _check_user_access(
        self,
        location_uuid: UUID,
        energy_source: dp.EnergySource,
        location_type: dp.LocationType,
        oauth_id: str,
    ) -> models.Location:
        """Check if a user has access to a given location."""
        req = dp.ListLocationsRequest(
            location_uuids_filter=[str(location_uuid)],
            energy_source_filter=energy_source,
            location_type_filter=location_type,
            user_oauth_id_filter=oauth_id,
        )
        resp = await self.dpc.list_locations(req)
        if len(resp.locations) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No location found for UUID {location_uuid} and OAuth ID {oauth_id}",
            )
        loc = resp.locations[0]

        return models.Location(
            uuid=loc.location_uuid,
            name=loc.location_name,
            capacity_kilowatts=loc.effective_capacity_watts / 1000.0,
            latitude=loc.latlng.latitude,
            longitude=loc.latlng.longitude,
            metadata=struct_to_dict(loc.metadata),
        )


# @override
# async def get_latest_forecast(
#     self,
#     authdata: dict[str, str],
#     location_uuid: UUID,
#     forecaster_name: str | None = None,
# ) -> models.Forecast:
#     """Get the latest forecast for a site."""
#     # get forecasters
#     request = dp.GetLatestForecastsRequest(
#         location_uuid=str(location_uuid),
#         energy_source=dp.EnergySource.SOLAR,
#     )
#     response = await self.dp.get_latest_forecasts(request)

#     # reduce to forecaster_name
#     if forecaster_name is not None:
#         response.forecasts = [
#             f for f in response.forecasts
#             if f.forecaster.forecaster_name == forecaster_name
#         ]

#     # shouldnt need this but just incase
#     response.forecasts.sort(
#         key=lambda f: f.created_timestamp_utc,
#         reverse=True,
#     )

#     return models.Forecast(
#         created_time=response.forecasts[0].created_timestamp_utc,
#         name=response.forecasts[0].forecaster.forecaster_name,
#         version=response.forecasts[0].forecaster.forecaster_version,
#     )


def struct_to_dict(values: Struct) -> dict:
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
