"""A data platform implementation that conforms to the DatabaseInterface."""
import datetime as dt
import logging
from uuid import UUID

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from google.protobuf.struct_pb2 import Struct
from ocf.dp.dp import common_pb2
from ocf.dp.dp_data import messages_pb2, service_pb2_grpc
from typing_extensions import override

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import get_oauth_id_from_sub

log = logging.getLogger("dataplatform.client")

energy_type_map: dict[models.EnergyType, common_pb2.EnergySource] = {
    models.EnergyType.SOLAR: common_pb2.EnergySource.ENERGY_SOURCE_SOLAR,
    models.EnergyType.WIND: common_pb2.EnergySource.ENERGY_SOURCE_WIND,
}

location_type_map: dict[models.LocationType, common_pb2.LocationType] = {
    models.LocationType.SITE: common_pb2.LocationType.LOCATION_TYPE_SITE,
    models.LocationType.GSP: common_pb2.LocationType.LOCATION_TYPE_GSP,
    models.LocationType.REGION: common_pb2.LocationType.LOCATION_TYPE_STATE,
    models.LocationType.NATION: common_pb2.LocationType.LOCATION_TYPE_NATION,
    models.LocationType.SUBSTATION: common_pb2.LocationType.LOCATION_TYPE_PRIMARY_SUBSTATION,
    models.LocationType.DNO: common_pb2.LocationType.LOCATION_TYPE_DNO,
}

dp_to_internal_location_type: dict[common_pb2.LocationType, models.LocationType] = {
    v: k for k, v in location_type_map.items()
}


_DP_SERVICE = "ocf.dp.DataPlatformDataService"

def struct_to_dict(pb_struct: Struct) -> dict[str, str | float | bool]:
    """Convert a protobuf struct to a python dictionary.

    Ignores any recursive elements, i.e. the struct has to be flat.
    """
    out = {}
    for key, value_msg in pb_struct.fields.items():
        kind = value_msg.WhichOneof("kind")

        if kind == "number_value":
            out[key] = value_msg.number_value
        elif kind == "string_value":
            out[key] = value_msg.string_value
        elif kind == "bool_value":
            out[key] = value_msg.bool_value

    return out


class StorageClient(models.StorageInterface):
    """Defines a data platform conneciton that conforms to the StorageInterface."""

    dpc: service_pb2_grpc.DataPlatformDataServiceStub

    @classmethod
    def from_dp(cls, dp_client: service_pb2_grpc.DataPlatformDataServiceStub) -> "StorageClient":
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
            created_cutoff = dt.datetime.now(tz=dt.UTC) - dt.timedelta(
                minutes=forecast_horizon_minutes,
            )

        oauth_id: str | None = (
            get_oauth_id_from_sub(authdata["sub"]) if authdata != {} else None
        )
        req = messages_pb2.ListLocationsRequest(
            location_uuids_filter=[str(location_uuid)],
            energy_source_filter=energy_type_map[energy_type],
            location_type_filter=location_type_map[location_type],
            user_oauth_id_filter=oauth_id,
        )
        resp = await self.dpc.ListLocations(req)
        if len(resp.locations) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No location found for UUID '{location_uuid}',\
                      {location_type} and {energy_type}",
            )
        location = resp.locations[0]

        if location_type == models.LocationType.SUBSTATION:
            # Get the GSP the substation belongs to
            req = messages_pb2.ListLocationsRequest(
                enclosed_location_uuid_filter=str(location_uuid),
                location_type_filter=common_pb2.LocationType.LOCATION_TYPE_GSP,
                user_oauth_id_filter=oauth_id,
            )
            gsps = await self.dpc.ListLocations(req)
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
            req = messages_pb2.GetLatestForecastsRequest(
                location_uuid=str(location_uuid),
                energy_source=energy_type_map[energy_type],
                pivot_timestamp_utc=window_start
                - dt.timedelta(minutes=forecast_horizon_minutes),
            )
            resp = await self.dpc.GetLatestForecasts(req)
            if len(resp.forecasts) == 0:
                return []
            resp.forecasts.sort(
                key=lambda f: f.created_timestamp_utc.ToDatetime(tzinfo=dt.UTC),
                reverse=True,
            )
            forecaster = resp.forecasts[0].forecaster
        elif forecaster_version is None:
            req = messages_pb2.ListForecastersRequest(
                forecaster_names_filter=[forecaster_name],
                latest_versions_only=True,
            )
            resp = await self.dpc.ListForecasters(req)
            if not resp.forecasters:
                raise HTTPException(
                    status_code=404,
                    detail=f"Forecast model '{forecaster_name}' not found in data platform.",
                )
            forecaster = resp.forecasters[0]
        else:
            forecaster = messages_pb2.Forecaster(
                forecaster_name=forecaster_name,
                forecaster_version=forecaster_version,
            )

        req = messages_pb2.GetForecastAsTimeseriesRequest(
            location_uuid=str(location_uuid),
            energy_source=energy_type_map[energy_type],
            horizon_mins=forecast_horizon_minutes,
            time_window=messages_pb2.TimeWindow(
                start_timestamp_utc=window_start,
                end_timestamp_utc=window_end,
            ),
            forecaster=forecaster,
            pivot_timestamp_utc=created_cutoff,
        )

        resp = await self.dpc.GetForecastAsTimeseries(req)

        if location_type == models.LocationType.SUBSTATION:
            # Spoof the forecast values so that the capacity and id corresponds to the substation
            resp.location_uuid = str(location_uuid)
            for v in resp.values:
                v.effective_capacity_watts = location.effective_capacity_watts

        def _map_resp(resp: messages_pb2.GetForecastAsTimeseriesResponse) \
                -> list[models.PredictedGenerationValue]:
            out: list[models.PredictedGenerationValue] = []
            for v in resp.values:
                plevels: dict[str, int | float] = {}
                stats = v.other_statistics_fractions
                for plevel in ["p10", "p90"]:
                    val = int(
                        v.effective_capacity_watts * stats[plevel] / 1000.0,
                    ) if plevel in stats else None
                    if val is not None:
                        plevels[plevel] = val

                out.append(models.PredictedGenerationValue(
                    power_kilowatts=int(
                        float(v.effective_capacity_watts) \
                            * float(v.p50_value_fraction) / 1000,
                    ),
                    valid_timestamp=v.target_timestamp_utc.ToDatetime(tzinfo=dt.UTC),
                    location_uuid=UUID(location_uuid) \
                        if isinstance(location_uuid, str) else location_uuid,
                    capacity_kilowatts=int(float(v.effective_capacity_watts) / 1000),
                    created_timestamp=v.created_timestamp_utc.ToDatetime(tzinfo=dt.UTC),
                    init_timestamp=v.initialization_timestamp_utc.ToDatetime(tzinfo=dt.UTC),
                    forecaster_name=forecaster.forecaster_name,
                    forecaster_version=forecaster.forecaster_version,
                    plevels_kilowatts=plevels,
                    metadata=struct_to_dict(v.metadata) if v.metadata is not None else {},
                ))

            return out

        return await run_in_threadpool(_map_resp, resp)


    @override
    async def put_predicted_generation(
        self,
        generation_values: list[models.PredictedGenerationValue],
        location_type: models.LocationType,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
    ) -> None:
        raise NotImplementedError(
            "Data platform backend doesn't support forecast input yet.",
        )

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

        req = messages_pb2.GetObservationsAsTimeseriesRequest(
            location_uuid=str(location_uuid),
            observer_name=observer_name,
            energy_source=energy_type_map[energy_type],
            time_window=messages_pb2.TimeWindow(
                start_timestamp_utc=window_start,
                end_timestamp_utc=window_end,
            ),
        )
        resp = await self.dpc.GetObservationsAsTimeseries(req)

        def _map_resp(resp: messages_pb2.GetObservationsAsTimeseriesResponse) \
                -> list[models.ActualGenerationValue]:
            out: list[models.ActualGenerationValue] = [
                models.ActualGenerationValue(
                    valid_timestamp=v.timestamp_utc.ToDatetime(tzinfo=dt.UTC),
                    power_kilowatts=int(
                        v.effective_capacity_watts * v.value_fraction / 1000.0,
                    ),
                    location_uuid=UUID(resp.location_uuid),
                    capacity_kilowatts=int(v.effective_capacity_watts / 1000.0),
                    observer_name=observer_name,
                )
                for v in resp.values
            ]
            return out

        return await run_in_threadpool(_map_resp, resp)

    @override
    async def put_actual_generation(
        self,
        generation_values: list[models.ActualGenerationValue],
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
    ) -> None:
        raise NotImplementedError(
            "Data platform backend does not yet support writing generation",
        )

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
        if forecaster_name is None:
            raise ValueError("Forecaster name must be specified for data platform backend.")
        if forecaster_version is None:
            req = messages_pb2.ListForecastersRequest(
                forecaster_names_filter=(
                    [forecaster_name] if forecaster_name is not None else []
                ),
                latest_versions_only=True,
            )
            resp = await self.dpc.ListForecasters(req)
            if not resp.forecasters:
                raise HTTPException(
                    status_code=404,
                    detail=f"Forecast model '{forecaster_name}' not found in data platform.",
                )
            forecaster = resp.forecasters[0]
        else:
            forecaster = messages_pb2.Forecaster(
                forecaster_name=forecaster_name,
                forecaster_version=forecaster_version,
            )

        req = messages_pb2.GetForecastAtTimestampRequest(
            location_uuids=[str(uuid) for uuid in location_uuids],
            energy_source=energy_type_map[energy_type],
            timestamp_utc=snapshot_timestamp_utc,
            forecaster=forecaster,
        )
        resp = await self.dpc.GetForecastAtTimestamp(req)

        def _map_resp(resp: messages_pb2.GetForecastAtTimestampResponse) \
                -> list[models.PredictedGenerationValue]:
            out: list[models.PredictedGenerationValue] = [
                models.PredictedGenerationValue(
                    power_kilowatts=int(v.value_fraction * v.effective_capacity_watts) / 1000,
                    valid_timestamp=resp.timestamp_utc.ToDatetime(tzinfo=dt.UTC),
                    location_uuid=UUID(v.location_uuid),
                    capacity_kilowatts=v.effective_capacity_watts / 1000,
                    forecaster_name=forecaster.forecaster_name,
                    forecaster_version=forecaster.forecaster_version,
                    created_timestamp=v.created_timestamp_utc.ToDatetime(tzinfo=dt.UTC),
                    init_timestamp=v.initialization_timestamp_utc.ToDatetime(tzinfo=dt.UTC),
                    metadata=struct_to_dict(v.metadata) if v.metadata is not None else {},
                )
                for v in resp.values
            ]
            return out

        return await run_in_threadpool(_map_resp, resp)

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

        req = messages_pb2.GetObservationsAtTimestampRequest(
            location_uuids=[str(uuid) for uuid in location_uuids],
            energy_source=energy_type_map[energy_type],
            timestamp_utc=snapshot_timestamp_utc,
            observer_name=observer_name,
        )
        resp = await self.dpc.GetObservationsAtTimestamp(req)

        def _map_resp(resp: messages_pb2.GetObservationsAtTimestampResponse) \
                -> list[models.ActualGenerationValue]:
            out: list[models.ActualGenerationValue] = [
                models.ActualGenerationValue(
                    valid_timestamp=resp.timestamp_utc.ToDatetime(tzinfo=dt.UTC),
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

        return await run_in_threadpool(_map_resp, resp)

    @override
    async def get_locations(
        self,
        energy_type: models.EnergyType,
        location_type: models.LocationType | None,
        authdata: dict[str, str],
        location_uuid: UUID | None = None,
        enclosing_location_uuid: UUID | None = None,
    ) -> list[models.Location]:
        # For the moment, recreate the auth behaviour of the old routes in here.
        # This should be delegated to the scoping on the API endpoints themselves later.
        match energy_type, location_type:
            case models.EnergyType.WIND, models.LocationType.REGION:
                # get_wind_regions had no auth
                oauth_id: str | None = None
            case (
                models.EnergyType.SOLAR,
                models.LocationType.NATION | models.LocationType.GSP,
            ):
                # get_solar_regions had no auth
                oauth_id = None
            case _, models.LocationType.SUBSTATION:
                # get substations had optional auth (?) (temporary while we onboard?)
                oauth_id = (
                    get_oauth_id_from_sub(authdata["sub"]) if authdata != {} else None
                )
            case _, models.LocationType.SITE:
                # get_sites had auth
                oauth_id = get_oauth_id_from_sub(authdata["sub"])
            case _, None:
                # No location type filter — used by v1 API for listing all region types
                oauth_id = (
                    get_oauth_id_from_sub(authdata["sub"]) if authdata != {} else None
                )
            case _:
                oauth_id = (
                    get_oauth_id_from_sub(authdata["sub"]) if authdata != {} else None
                )

        req = messages_pb2.ListLocationsRequest(
            energy_source_filter=energy_type_map[energy_type],
            location_type_filter=(
                location_type_map[location_type] if location_type is not None else None
            ),
            user_oauth_id_filter=oauth_id,
            location_uuids_filter=(
                [str(location_uuid)] if location_uuid is not None else []
            ),
            enclosing_location_uuid_filter=(
                str(enclosing_location_uuid)
                if enclosing_location_uuid is not None
                else None
            ),
        )
        resp = await self.dpc.ListLocations(req)

        def _map_resp(resp: messages_pb2.ListLocationsResponse) -> list[models.Location]:
            out: list[models.Location] = [
                models.Location(
                    uuid=UUID(loc.location_uuid),
                    name=loc.location_name,
                    capacity_kilowatts=loc.effective_capacity_watts / 1000.0,
                    latitude=loc.latlng.latitude,
                    longitude=loc.latlng.longitude,
                    location_type=dp_to_internal_location_type.get(loc.location_type),
                    metadata=struct_to_dict(loc.metadata) if loc.metadata is not None else {},
                )
                for loc in resp.locations
            ]
            return out

        return await run_in_threadpool(_map_resp, resp)

    @override
    async def put_location(
        self,
        location: models.Location,
        location_type: models.LocationType,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
    ) -> models.Location:
        raise NotImplementedError(
            "Data platform backend doesn't yet support location writing.",
        )

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
        energy_source: common_pb2.EnergySource,
        location_type: common_pb2.LocationType,
        oauth_id: str,
    ) -> models.Location:
        """Check if a user has access to a given location."""
        req = messages_pb2.ListLocationsRequest(
            location_uuids_filter=[str(location_uuid)],
            energy_source_filter=energy_source,
            location_type_filter=location_type,
            user_oauth_id_filter=oauth_id,
        )
        resp = await self.dpc.ListLocations(req)
        if len(resp.locations) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No location found for UUID {location_uuid} and OAuth ID {oauth_id}",
            )
        loc = resp.locations[0]

        return models.Location(
            uuid=UUID(loc.location_uuid),
            name=loc.location_name,
            capacity_kilowatts=loc.effective_capacity_watts / 1000.0,
            latitude=loc.latlng.latitude,
            longitude=loc.latlng.longitude,
            metadata=struct_to_dict(loc.metadata) if loc.metadata is not None else {},
        )


