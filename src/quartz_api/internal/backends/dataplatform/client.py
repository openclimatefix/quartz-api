"""A data platform implementation that conforms to the DatabaseInterface."""
import asyncio
import datetime as dt
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from google.protobuf.struct_pb2 import Struct
from google.protobuf.timestamp_pb2 import Timestamp
from ocf.dp.dp import common_pb2
from ocf.dp.dp_data import messages_pb2, service_pb2_grpc
from timezonefinder import timezone_at
from typing_extensions import override

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import get_org_id_from_authdata

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


def dict_to_struct(d: dict[str, str | int | float | bool]) -> Struct:
    """Convert a flat python dictionary into a protobuf Struct."""
    s = Struct()
    s.update(d)
    return s


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
        day_ahead: bool = False,
        day_ahead_closure_time_local: dt.time | None = None,
    ) -> list[models.PredictedGenerationValue]:

        # Limit the creation time if not set
        forecaster_pivot = created_cutoff
        if created_cutoff is None:
            forecaster_pivot = dt.datetime.now(tz=dt.UTC)
            created_cutoff = forecaster_pivot - dt.timedelta(
                minutes=forecast_horizon_minutes,
            )

        organisation_id: str | None = get_org_id_from_authdata(authdata) if authdata != {} else None
        if organisation_id == "no-org-access":
            if location_type == models.LocationType.SITE:
                raise HTTPException(
                    status_code=403,
                    detail="User is not associated with any organization.",
                )
            else:
                organisation_id = None
        req = messages_pb2.ListLocationsRequest(
            location_uuids_filter=[str(location_uuid)],
            energy_source_filter=energy_type_map[energy_type],
            location_type_filter=location_type_map[location_type],
            organisation_id_filter=organisation_id,
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
            # Pick whichever forecaster wrote most recently, as of now rather than the
            # horizon cutoff, ties settled by name — so changing horizon can't change model.
            req = messages_pb2.GetLatestForecastsRequest(
                location_uuid=str(location_uuid),
                energy_source=energy_type_map[energy_type],
                pivot_timestamp_utc=forecaster_pivot,
            )
            resp = await self.dpc.GetLatestForecasts(req)
            if len(resp.forecasts) == 0:
                return []
            resp.forecasts.sort(
                key=lambda f: (
                    -f.created_timestamp_utc.ToDatetime(tzinfo=dt.UTC).timestamp(),
                    f.forecaster.forecaster_name,
                ),
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

        if not day_ahead:
            windows = [{"start": window_start,
                "end": window_end,
                "pivot_timestamp_utc": created_cutoff},
                ]

        else:
            # We are now getting the DA forecaster,
            # this is made up of chunks of different forecasts
            # for each day in the day-ahead window.

            tz = timezone_at(lng=location.latlng.longitude, lat=location.latlng.latitude)
            windows = make_day_ahead_windows(window_start=window_start,
                                             window_end=window_end,
                                             tz_string=tz,
                                             day_ahead_closure_time_local=day_ahead_closure_time_local,
                                             created_cutoff=created_cutoff)

        reqs = [
            messages_pb2.GetForecastAsTimeseriesRequest(
                location_uuid=str(location_uuid),
                energy_source=energy_type_map[energy_type],
                horizon_mins=forecast_horizon_minutes,
                time_window=messages_pb2.TimeWindow(
                    start_timestamp_utc=window["start"],
                    end_timestamp_utc=window["end"],
                ),
                forecaster=forecaster,
                pivot_timestamp_utc=window["pivot_timestamp_utc"],
            )
            for window in windows
        ]

        resps = await asyncio.gather(
            *(self.dpc.GetForecastAsTimeseries(req) for req in reqs),
        )

        values = []
        for resp in resps:
            if location_type == models.LocationType.SUBSTATION:
                # Spoof the forecast values so that the capacity
                # and id corresponds to the substation
                resp.location_uuid = str(location_uuid)
                for v in resp.values:
                    v.effective_capacity_watts = location.effective_capacity_watts

            values += resp.values

        def _map_resp(values: list[messages_pb2.GetForecastAsTimeseriesResponse.Value]) \
                -> list[models.PredictedGenerationValue]:
            out: list[models.PredictedGenerationValue] = []
            for v in values:
                plevels: dict[str, int | float] = {
                    f"p{int(plevel[1:])}": int(v.effective_capacity_watts * frac / 1000.0)
                    for plevel, frac in v.other_statistics_fractions.items()
                }

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

        return await run_in_threadpool(_map_resp, values)

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
                org_id=get_org_id_from_authdata(authdata),
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
        location_uuid: UUID | str,
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
    ) -> None:
        if not generation_values:
            return

        if authdata != {}:
            _ = await self._check_user_access(
                location_uuid=location_uuid,
                energy_source=energy_type_map[energy_type],
                location_type=location_type_map[location_type],
                org_id=get_org_id_from_authdata(authdata),
            )

        observer_name = generation_values[0].observer_name

        req = messages_pb2.CreateObservationsRequest(
            location_uuid=str(location_uuid),
            energy_source=energy_type_map[energy_type],
            observer_name=observer_name,
            values=[
                messages_pb2.CreateObservationsRequest.Value(
                    timestamp_utc=gv.valid_timestamp,
                    value_watts=max(0, int(gv.power_kilowatts * 1000)),
                )
                for gv in generation_values
            ],
        )
        await self.dpc.CreateObservations(req)

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
        energy_type: models.EnergyType | None,
        location_type: models.LocationType | None,
        authdata: dict[str, str],
        location_uuid: UUID | None = None,
        enclosing_location_uuid: UUID | None = None,
        location_names: list[str] | None = None,
    ) -> list[models.Location]:
        # For the moment, recreate the auth behaviour of the old routes in here.
        # This should be delegated to the scoping on the API endpoints themselves later.
        match energy_type, location_type:
            case models.EnergyType.WIND, models.LocationType.REGION:
                # get_wind_regions had no auth
                organisation_id: str | None = None
            case (
                models.EnergyType.SOLAR,
                models.LocationType.NATION | models.LocationType.GSP | models.LocationType.REGION,
            ):
                # get_solar_regions had no auth
                organisation_id = None
            case _, models.LocationType.SUBSTATION:
                # get substations had optional auth (?) (temporary while we onboard?)
                organisation_id = (
                    get_org_id_from_authdata(authdata) if authdata != {} else None
                )
            case _, models.LocationType.SITE:
                # get_sites had auth (HubSpot company ID)
                organisation_id = (
                    get_org_id_from_authdata(authdata) if authdata != {} else None
                )
            case _, None:
                # No location type filter — used by v1 API for listing all region types
                organisation_id = (
                    get_org_id_from_authdata(authdata) if authdata != {} else None
                )
            case _:
                organisation_id = (
                    get_org_id_from_authdata(authdata) if authdata != {} else None
                )

        if organisation_id == "no-org-access":
            if location_type == models.LocationType.SITE:
                if location_uuid is not None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"No site found for UUID '{location_uuid}'.",
                    )
                return []
            else:
                organisation_id = None

        req = messages_pb2.ListLocationsRequest(
            energy_source_filter=(
                energy_type_map[energy_type] if energy_type is not None else None
            ),
            location_type_filter=(
                location_type_map[location_type] if location_type is not None else None
            ),
            organisation_id_filter=organisation_id,
            location_uuids_filter=(
                [str(location_uuid)] if location_uuid is not None else []
            ),
            enclosing_location_uuid_filter=(
                str(enclosing_location_uuid)
                if enclosing_location_uuid is not None
                else None
            ),
            location_names_filter=location_names or [],
        )
        resp = await self.dpc.ListLocations(req)

        # Reverse map from proto EnergySource -> internal EnergyType
        dp_to_internal_energy_type: dict[common_pb2.EnergySource, models.EnergyType] = {
            v: k for k, v in energy_type_map.items()
        }

        def _map_resp(resp: messages_pb2.ListLocationsResponse) -> list[models.Location]:
            out: list[models.Location] = [
                models.Location(
                    uuid=UUID(loc.location_uuid),
                    name=loc.location_name,
                    capacity_kilowatts=loc.effective_capacity_watts / 1000.0,
                    latitude=loc.latlng.latitude,
                    longitude=loc.latlng.longitude,
                    location_type=dp_to_internal_location_type.get(loc.location_type),
                    energy_type=dp_to_internal_energy_type.get(loc.energy_source),
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
        existing = await self.get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata=authdata,
            location_uuid=location.uuid,
        )
        if not existing:
            create_req = messages_pb2.CreateLocationRequest(
                location_name=location.name,
                energy_source=energy_type_map[energy_type],
                geometry_wkt=f"POINT ({location.longitude} {location.latitude})",
                effective_capacity_watts=int(location.capacity_kilowatts * 1000),
                location_type=location_type_map[location_type],
                metadata=dict_to_struct(location.metadata),
            )
            created = await self.dpc.CreateLocation(create_req)
            return models.Location(
                uuid=UUID(created.location_uuid),
                name=created.location_name,
                latitude=location.latitude,
                longitude=location.longitude,
                capacity_kilowatts=created.effective_capacity_watts / 1000.0,
                location_type=location_type,
                metadata=location.metadata,
            )
        current = existing[0]

        """new_metadata replaces existing metadata entirely, so merge new values over the
        current.
        """
        merged_metadata = {**current.metadata, **location.metadata}
        valid_from_utc = Timestamp()
        valid_from_utc.GetCurrentTime()

        req = messages_pb2.UpdateLocationRequest(
            location_uuid=str(location.uuid),
            energy_source=energy_type_map[energy_type],
            new_effective_capacity_watts=int(location.capacity_kilowatts * 1000),
            new_metadata=dict_to_struct(merged_metadata),
            valid_from_utc=valid_from_utc,
        )
        if location.name:
            req.new_location_name = location.name

        resp = await self.dpc.UpdateLocation(req)

        return models.Location(
            uuid=UUID(resp.location_uuid),
            name=resp.location_name,
            latitude=current.latitude,
            longitude=current.longitude,
            capacity_kilowatts=resp.effective_capacity_watts / 1000.0,
            location_type=location_type,
            metadata=merged_metadata,
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
        org_id: str | None,
    ) -> models.Location:
        """Check if an organisation has access to a given location."""
        if org_id == "no-org-access":
            if location_type == common_pb2.LocationType.LOCATION_TYPE_SITE:
                raise HTTPException(
                    status_code=403,
                    detail="User is not associated with any organization.",
                )
            else:
                org_id = None
        req = messages_pb2.ListLocationsRequest(
            location_uuids_filter=[str(location_uuid)],
            energy_source_filter=energy_source,
            location_type_filter=location_type,
            organisation_id_filter=org_id,
        )
        resp = await self.dpc.ListLocations(req)
        if len(resp.locations) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No location found for UUID {location_uuid} and org ID {org_id}",
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


def make_day_ahead_windows(window_start: dt.datetime,
                           window_end: dt.datetime,
                           tz_string: str,
                           day_ahead_closure_time_local: dt.time,
                           created_cutoff: dt.datetime | None = None) -> \
                           list[dict[str, messages_pb2.TimeWindow | dt.datetime]]:
    """Make the day-ahead time windows for a given time range.

    For a given period of time, the DA forecast is broken into several forecast.
    Here we define those windows.

    1. Change start and end times to local time zone.
    2. Take the dates from the local time zone.
    3. Create list of start and end windows in local time
    4. Change back to UTC
    5. Create pivot_timestamp_utc based of the market closing time
    6. Return all windows

    Examples:
    UK (winter): From 2026-02-02 09:00 to 2026-02-02 18:00, DA market closing time is 09:00
    We get a window from 2026-02-02 09:00 to 2026-02-02 18:00
    and a pivot timestamp of 2026-02-01 09:00

    UK (summer): From 2026-07-02 09:00 to 2026-07-03 18:00, DA market closing time is 09:00
    We get a windows
    1. 2026-07-02 09:00 to 2026-07-02 23:00 and a pivot timestamp of 2026-07-01 08:00
    2. 2026-07-02 23:00 to 2026-07-03 18:00 and a pivot timestamp of 2026-07-02 08:00

    IST: From 2026-07-02 09:00 to 2026-07-03 18:00, DA market closing time is 09:00
    We get a windows
    1. 2026-07-02 09:00 to 2026-07-02 18:30 and a pivot timestamp of 2026-07-01 03:30
    2. 2026-07-02 18:30 to 2026-07-03 18:00 and a pivot timestamp of 2026-07-02 03:30

    """
    # change tz string to timezone, e.g. "Europe/London"
    tz = ZoneInfo(tz_string)

    # change to local time zones and take the dates
    window_start_local = window_start.astimezone(tz).date()
    window_end_local = window_end.astimezone(tz).date()

    if window_end.astimezone(tz).time() != dt.time.min:
        window_end_local = window_end_local + dt.timedelta(days=1)

    # convert from dates to datetimes
    window_start_local = dt.datetime.combine(window_start_local, dt.time.min, tzinfo=tz)
    window_end_local = dt.datetime.combine(window_end_local, dt.time.min, tzinfo=tz)

    N = (window_end_local - window_start_local).days
    windows = []
    for i in range(0, N, 1):
        start = window_start_local + dt.timedelta(days=i)
        end = window_start_local + dt.timedelta(days=i+1)

        if i < N - 1:
            # this is to make sure windows dont overlap
            end = end - dt.timedelta(seconds=1)

        # convert back to utc
        start = start.astimezone(dt.UTC)
        end = end.astimezone(dt.UTC)

        # clip to start and end of window
        start = max(start, window_start)
        end = min(end, window_end)

        # lets calculate the time at which the latest forecast can be loaded from.
        # e.g for UK 2026-02-02, the latest DA forecast is made before 2026-01-01 09:00
        pivot_timestamp_local = dt.datetime.combine(
            (start.astimezone(tz).date() - dt.timedelta(days=1)),
            day_ahead_closure_time_local, tzinfo=tz)
        pivot_timestamp_utc = pivot_timestamp_local.astimezone(dt.UTC)
        # make sure we don't go past the created cutoff
        if created_cutoff is not None:
            pivot_timestamp_utc = min(pivot_timestamp_utc, created_cutoff)

        windows.append({"start": start,
            "end": end,
            "pivot_timestamp_utc": pivot_timestamp_utc,
        })


    return windows
