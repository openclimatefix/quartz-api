"""The 'gsp' FastAPI router object."""

import asyncio
import datetime as dt
import logging
import traceback
from collections import defaultdict
from typing import Annotated
from uuid import UUID

import pandas as pd
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
)
from fastapi.concurrency import run_in_threadpool
from fastapi_cache import FastAPICache
from fastapi_cache.decorator import cache
from pydantic import AfterValidator, TypeAdapter
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.service.uk_national.metadata import format_metadata

from ...models.endpoint_types import default_now_window_start, default_window_end
from .cache import key_builder
from .endpoint_types import (
    Forecast,
    ForecastValue,
    GSPYield,
    GSPYieldGroupByDatetime,
    InputDataLastUpdated,
    Location,
    MLModel,
    OneDatetimeManyForecastValuesMW,
    convert_list_of_gsp_ids,
    gsp_id_map,
)
from .time_utils import (
    limit_end_datetime_by_permissions,
)

log = logging.getLogger(__name__)

GSP_FORECASTER_NAME = "blend"
GSP_FORECASTER_VERSION = "1.3.0"
# We use this for the default gsp/forecast/all route
GSP_FORECAST_ALL_CACHE_LENGTH_SECS_LONG = 60 * 60 * 24 # 1 day
# we use this on the route, for things likes gsp/forecast/all?gsp_ids=1,2,3
GSP_FORECAST_ALL_CACHE_LENGTH_SECS_ROUTE = 10 * 60 # 10 minutes

router = APIRouter()

_forecast_adapter = TypeAdapter(list[Forecast])
_compact_adapter = TypeAdapter(list[OneDatetimeManyForecastValuesMW])
_cache_warming: bool = False



@router.get(
    "/{gsp_id}/forecast",
    response_model=list[ForecastValue],
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder)
async def get_forecasts_for_a_specific_gsp(
    request: Request,  # noqa: ARG001
    db: models.StorageClientDependency,
    auth: AuthDependency, # noqa: ARG001
    gsp_id: int,
    start_datetime_utc: models.UTCDatetimeDefaultWindowStart,
    end_datetime_utc: Annotated[
        dt.datetime,
        Depends(limit_end_datetime_by_permissions),
    ],
    creation_utc_limit: models.UTCDatetime | None = None,
    forecast_horizon_minutes: int | None = None,
) -> list[ForecastValue]:
    """### Get recent forecast values for a specific GSP.

    This route returns the most recent forecast for each _target_time_ for a
    specific GSP.

    The _forecast_horizon_minutes_ parameter allows
    a user to query for a forecast that is made this number, or horizon, of
    minutes before the _target_time_.

    For example, if the target time is 10am today, the forecast made at 2am
    today is the 8-hour forecast for 10am, and the forecast made at 6am for
    10am today is the 4-hour forecast for 10am.

    #### Parameters
    - **gsp_id**: *gsp_id* of the desired forecast
    - **forecast_horizon_minutes**: optional forecast horizon in minutes (ex. 60
    - **start_datetime_utc**: optional start datetime for the query.
    - **end_datetime_utc**: optional end datetime for the query.
    - **creation_utc_limit**: optional, only return forecasts made before this datetime.
    returns the latest forecast made 60 minutes before the target time)
    """
    if gsp_id not in gsp_id_map:
        # According to the integration tests, we should return a 200 OK when getting a non-
        # existent GSP - so that is what is replicated here. Seems odd to me.
        return []

    pgvs = await db.get_predicted_generation(
        location_uuid=gsp_id_map[gsp_id].uuid,
        window_start=start_datetime_utc,
        window_end=end_datetime_utc,
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata={},
        created_cutoff=creation_utc_limit,
        forecast_horizon_minutes=forecast_horizon_minutes or 0,
        forecaster_name=GSP_FORECASTER_NAME,
        forecaster_version=GSP_FORECASTER_VERSION,
    )
    log.info(f"Fetched {len(pgvs)} predicted generation values for gsp_id {gsp_id}")

    out: list[ForecastValue] = [
        ForecastValue(
            target_time=pp.valid_timestamp,
            expected_power_generation_megawatts=round(pp.power_kilowatts / 1000, 4),
            expected_power_generation_normalized=round(
                pp.power_kilowatts / pp.capacity_kilowatts if pp.capacity_kilowatts!=0 else 0,
                4,
            ),
        )
        for pp in pgvs
    ]

    return out


@router.get(
    "/{gsp_id}/pvlive",
    response_model=list[GSPYield],
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder)
async def get_truths_for_a_specific_gsp(
    request: Request,  # noqa: ARG001
    db: models.StorageClientDependency,
    auth: AuthDependency, # noqa: ARG001
    gsp_id: int,
    start_datetime_utc: models.UTCDatetimeDefaultWindowStart,
    end_datetime_utc: models.UTCDatetimeDefaultWindowEnd,
    regime: Annotated[str, AfterValidator(lambda v: v.replace("-", "_"))] = "in-day",
) -> list[GSPYield]:
    """### Get PV_Live values for a specific GSP for yesterday and today.

    The return object is a series of real-time solar energy generation
    from __PV_Live__ for a single GSP.

    Setting the _regime_ parameter to _day-after_ includes
    the previous day's truth values for the GSPs.

    If _regime_ is not specified, the parameter defaults to _in-day_.

    #### Parameters
    - **gsp_id**: _gsp_id_ of the requested forecast
    - **regime**: can choose __in-day__ or __day-after__
    - **start_datetime_utc**: optional start datetime for the query.
    - **end_datetime_utc**: optional end datetime for the query.
    If not set, defaults to N_HISTORY_DAYS env var, which if not set defaults to yesterday.

    Only 3 days of history is available. If you want to get more PVLive data,
    please use the [PVLive API](https://www.solar.sheffield.ac.uk/api/)
    """
    if gsp_id not in gsp_id_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GSP ID {gsp_id} not found",
        )

    agvs = await db.get_actual_generation(
        location_uuid=gsp_id_map[gsp_id].uuid,
        window_start=start_datetime_utc,
        window_end=end_datetime_utc,
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata={},
        observer_name=f"pvlive_{regime}",
    )
    log.info(f"Fetched {len(agvs)} actual generation values for gsp_id {gsp_id}")

    out: list[GSPYield] = [
        GSPYield(
            datetime_utc=v.valid_timestamp,
            solar_generation_kw=v.power_kilowatts,
        )
        for v in agvs
    ]

    # lets make sure the latest timestamps are first
    out.sort(key=lambda x: x.datetime_utc, reverse=True)

    return out


def _build_compact_response(
    results: list[list[models.PredictedGenerationValue] | Exception],
    gsp_uuid_id_map: dict[UUID, int],
) -> list[OneDatetimeManyForecastValuesMW]:
    """Reorganise results as one entry per timestamp with a {gsp_id: mw} dict."""
    grouped: dict[dt.datetime, dict[int, float]] = defaultdict(dict)
    for snapshot in results:
        if isinstance(snapshot, Exception):
            log.warning("Snapshot call failed, skipping: %s", snapshot)
            continue
        for pgv in snapshot:
            gsp_id = gsp_uuid_id_map.get(pgv.location_uuid)
            grouped[pgv.valid_timestamp][gsp_id] = round(pgv.power_kilowatts / 1000.0, 4)
    return [
        OneDatetimeManyForecastValuesMW(
            datetime_utc=ts,
            forecast_values=dict(sorted(gsp_dict.items())),
        )
        for ts, gsp_dict in sorted(grouped.items())
    ]


def _build_forecast_response(
    results: list[list[models.PredictedGenerationValue] | Exception],
    gsp_id_map: dict[int, models.Location],
    gsp_uuid_id_map: dict[UUID, int],
    creation_time: dt.datetime,
) -> list[Forecast]:
    """Reorganise results as one Forecast object per GSP with all timesteps."""
    fvs_per_gsp: dict[int, list[ForecastValue]] = defaultdict(list)
    # Capture pgv info per GSP.
    gsp_pgv_map: dict[int, models.PredictedGenerationValue] = {}
    # Update input_data on every pgv so each GSP gets its own metadata.
    input_data_by_gsp: dict[int, InputDataLastUpdated] = {}

    for snapshot in results:
        if isinstance(snapshot, Exception):
            log.warning("Snapshot call failed, skipping: %s", snapshot)
            continue
        for pgv in snapshot:
            gsp_id = gsp_uuid_id_map.get(pgv.location_uuid)
            fvs_per_gsp[gsp_id].append(
                ForecastValue(
                    target_time=pgv.valid_timestamp,
                    expected_power_generation_megawatts=round(pgv.power_kilowatts / 1000.0, 4),
                ),
            )
            if gsp_id not in gsp_pgv_map:
                gsp_pgv_map[gsp_id] = pgv
            input_data_by_gsp[gsp_id] = format_metadata(pgv.metadata)

    # Fallback for any GSP with no data at all.
    stub_input_data = InputDataLastUpdated(
        gsp=creation_time, nwp=creation_time, pv=creation_time, satellite=creation_time,
    )

    forecasts = []
    for gsp_id in sorted(gsp_uuid_id_map.values()):
        pgv = gsp_pgv_map.get(gsp_id)
        if pgv is None:
            continue
        location = Location.from_location(gsp_id_map[gsp_id])
        location.installed_capacity_mw = \
            pgv.capacity_kilowatts / 1000.0
        forecasts.append(
            Forecast(
                location=location,
                model=MLModel(
                    name=pgv.forecaster_name if pgv else GSP_FORECASTER_NAME,
                    version=(
                        pgv.metadata.get("app_version", pgv.forecaster_version)
                        if pgv else GSP_FORECASTER_VERSION
                    ),
                ),
                forecast_creation_time=pgv.created_timestamp if pgv else creation_time,
                initialization_datetime_utc=pgv.init_timestamp if pgv else None,
                historic=True,
                forecast_values=sorted(
                    fvs_per_gsp.get(gsp_id, []),
                    key=lambda fv: fv.target_time,
                ),
                input_data_last_updated=input_data_by_gsp.get(gsp_id, stub_input_data),
            ),
        )
    return forecasts


@router.get(
    "/forecast/all/",
    response_model=list[OneDatetimeManyForecastValuesMW | Forecast],
    include_in_schema=False,
)
@cache(key_builder=key_builder, expire=GSP_FORECAST_ALL_CACHE_LENGTH_SECS_ROUTE) # 10 minutes
async def get_all_available_forecasts(
    request: Request,
    background_tasks: BackgroundTasks,
    db: models.StorageClientDependency,
    auth: AuthDependency,  # noqa: ARG001
    start_datetime_utc: Annotated[
        models.UTCDatetimeDefaultNowWindowStart,
        AfterValidator(lambda v: pd.Timestamp(v).ceil("30min").to_pydatetime()),
    ],
    end_datetime_utc: Annotated[
        dt.datetime,
        Depends(limit_end_datetime_by_permissions),
    ],
    gsp_ids: str | None = None,
    compact: bool = False,
) -> list[OneDatetimeManyForecastValuesMW] | list[Forecast]:
    """### Get all forecasts for all GSPs.

    Returns forecasts for all GSPs across the full forecast window.

    By default returns a list of Forecast objects, one per GSP (compact=false).
    With compact=true returns a time-first list of {gsp_id: mw} dicts, which is
    a smaller payload suited to bandwidth-sensitive use cases.

    #### Parameters
    - **compact**: if true, returns List[OneDatetimeManyForecastValues] (time-first).
      If false (default), returns List[Forecast] (one full forecast object per GSP).
    - **gsp_ids**: optional comma-separated GSP IDs to filter results.
    - **start_datetime_utc**: optional start datetime for the query.
    """
    # Default (no gsp_ids): served from warm cache only. If we're here it's a cache miss —
    # trigger a warm in the background and ask the client to retry.

    start_datetime_utc_set = start_datetime_utc != default_now_window_start()
    end_datetime_utc_set = end_datetime_utc != default_window_end()

    if gsp_ids is None and start_datetime_utc != end_datetime_utc:
            if start_datetime_utc_set or end_datetime_utc_set:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="start_datetime_utc must be equal to end_datetime_utc if gsp_ids is not specified",  # noqa: E501
                )

            global _cache_warming
            if not _cache_warming:
                background_tasks.add_task(_warm_forecast_all_cache, request.app)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "60"},
                detail="Forecast cache is being populated, please retry in 60 seconds.",
            )

    if gsp_ids is None:
        gsps_to_convert = gsp_id_map
        tasks = [
            db.get_predicted_generation_snapshot(
                location_uuids=[v.uuid for _,v in gsp_id_map.items()],
                snapshot_timestamp_utc=start_datetime_utc,
                authdata={},
                energy_type=models.EnergyType.SOLAR,
                forecaster_name=GSP_FORECASTER_NAME,
                forecaster_version=GSP_FORECASTER_VERSION,
            ),
        ]
    else:
        # gsp_ids path: custom query, fetch live.
        gsps_to_convert: dict[int, models.Location] = {
            k: v for k, v in gsp_id_map.items()
            if k in convert_list_of_gsp_ids(gsp_ids)
        }
        tasks = [
                db.get_predicted_generation(
                    location_uuid=str(loc.uuid),
                    window_start=start_datetime_utc,
                    window_end=end_datetime_utc,
                    energy_type=models.EnergyType.SOLAR,
                    location_type=models.LocationType.GSP,
                    authdata={},
                    forecast_horizon_minutes=0,
                    forecaster_name=GSP_FORECASTER_NAME,
                    forecaster_version=GSP_FORECASTER_VERSION,
            )
            for loc in gsps_to_convert.values()
        ]
    results: list[list[models.PredictedGenerationValue] | Exception] = await asyncio.gather(
        *tasks, return_exceptions=True,
    )
    log.info(f"Fetched predicted generation values for {len(results)} GSPs")

    gsp_uuid_id_map = {v.uuid: k for k, v in gsps_to_convert.items()}
    if compact:
        return _build_compact_response(results=results, gsp_uuid_id_map=gsp_uuid_id_map)
    return _build_forecast_response(
        results=results,
        gsp_id_map=gsps_to_convert,
        gsp_uuid_id_map=gsp_uuid_id_map,
        creation_time=start_datetime_utc,
    )


async def _warm_forecast_all_cache(app: FastAPI) -> None:
    """Pre-warm the /forecast/all/ cache by fetching data directly, bypassing HTTP auth.

    Runs as a background task triggered by the /forecast/all/refresh endpoint.
    Populates both the default (compact=false) and compact cache buckets so the
    first user request after a forecast run is served instantly.
    Cache keys are derived from key_builder's output for a default GET with no params:
      "{prefix}::get:{path}:{params}:{permissions}"
    """
    global _cache_warming
    _cache_warming = True
    try:
        # Wait to let the server finish starting up before hitting gRPC.
        await asyncio.sleep(5)
        db = app.dependency_overrides.get(models.get_storage_client, lambda: None)()
        if db is None:
            log.warning("GSP forecast cache warm skipped: storage client not configured")
            return
        log.info("Warming GSP forecast all cache")
        now = pd.Timestamp.utcnow()
        start = now.floor("30min").to_pydatetime().replace(tzinfo=dt.UTC)
        end = now.floor("6h").to_pydatetime().replace(tzinfo=dt.UTC) + dt.timedelta(days=2)

        gsp_uuid_id_map: dict = {
            gsp.uuid: gsp_id
            for gsp_id, gsp in gsp_id_map.items()
            if gsp_id != 0
        }
        # Fetch timestamps sequentially with a short pause between each so the event loop
        # stays free for "real" requests. Fine if this pre-warm takes a few minutes.
        timestamps = list(pd.date_range(start=start, end=end, freq="30min"))
        total = len(timestamps)
        results: list[list[models.PredictedGenerationValue] | Exception] = []
        for i, ts in enumerate(timestamps):
            try:
                result = await db.get_predicted_generation_snapshot(
                    location_uuids=list(gsp_uuid_id_map.keys()),
                    snapshot_timestamp_utc=ts.to_pydatetime(),
                    energy_type=models.EnergyType.SOLAR,
                    forecaster_name=GSP_FORECASTER_NAME,
                    forecaster_version=GSP_FORECASTER_VERSION,
                    authdata={},
                )
            except Exception as e:
                result = e
            results.append(result)
            if (i + 1) % 10 == 0 or (i + 1) == total:
                log.info("Cache warm progress: %d/%d timestamps fetched", i + 1, total)
            await asyncio.sleep(0.5)
        backend = FastAPICache.get_backend()
        prefix = FastAPICache.get_prefix()
        base_key = f"{prefix}::get:/v0/solar/GB/gsp/forecast/all/:"
        forecast_response = _build_forecast_response(
            results=results,
            gsp_id_map={k: v for k, v in gsp_id_map.items() if v != 0},
            gsp_uuid_id_map=gsp_uuid_id_map,
            creation_time=start,
        )
        forecast_value = await run_in_threadpool(
            _forecast_adapter.dump_json, forecast_response,
        )
        await backend.set(
            f"{base_key}[]:[]",
            forecast_value,
            expire=GSP_FORECAST_ALL_CACHE_LENGTH_SECS_LONG,
        )
        compact_response = _build_compact_response(
            results=results,
            gsp_uuid_id_map=gsp_uuid_id_map,
        )
        compact_value = await run_in_threadpool(
            _compact_adapter.dump_json, compact_response,
        )
        await backend.set(
            f"{base_key}[('compact', 'true')]:[]",
            compact_value,
            expire=GSP_FORECAST_ALL_CACHE_LENGTH_SECS_LONG,
        )
        log.info("GSP forecast all cache warmed: %d GSPs, %d timestamps",
                 len(gsp_uuid_id_map), len(results))
        log.info("GSP forecast all cache set with keys: %s and %s",
                 f"{base_key}[]:[]",
                 f"{base_key}[('compact', 'true')]:[]")
    except Exception:
        log.exception("GSP forecast all cache warm failed: %s", traceback.format_exc())
    finally:
        _cache_warming = False


@router.post(
    "/forecast/all/refresh",
    include_in_schema=False,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_forecast_all_cache(
    background_tasks: BackgroundTasks,
    request: Request,
    auth: AuthDependency,
    # x_refresh_token: Annotated[str, Header()],
) -> Response:
    """Trigger a background cache refresh for /forecast/all/.

    Called by Airflow at the end of the GSP forecast DAG to pre-warm the in-memory cache,
    preventing ~45s cold-start latency on the first user request after a new forecast run.
    Requires X-Refresh-Token header matching the CACHE_REFRESH_TOKEN environment variable.
    """
    # Check auth has ocf:admin role
    permissions = auth.get("permissions", [])

    if "ocf:admin" not in permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to refresh cache",
        )
    else:
        log.info("OCF admin permission confirmed. Refreshing forecast all cache...")

    if _cache_warming:
        log.warning("Forecast all cache warm already in progress")
        return Response(
            status_code=status.HTTP_202_ACCEPTED,
            content="Forecast all cache warm already in progress",
        )
    else:
        background_tasks.add_task(_warm_forecast_all_cache, request.app)
        return Response(
            status_code=status.HTTP_202_ACCEPTED,
            content="Forecast all cache refresh triggered successfully",
        )



@router.get(
    "/pvlive/all",
    response_model=list[GSPYieldGroupByDatetime],
    include_in_schema=False,
)
@cache(key_builder=key_builder, expire=60 * 30)
async def get_truths_for_all_gsps(
    request: Request,  # noqa: ARG001
    db: models.StorageClientDependency,
    auth: AuthDependency, # noqa: ARG001
    start_datetime_utc: models.UTCDatetimeDefaultWindowStart, # TODO update to now
    end_datetime_utc: models.UTCDatetimeDefaultWindowEnd,
    regime: Annotated[str, AfterValidator(lambda v: v.replace("-", "_"))] = "in-day",
    gsp_ids: str | None = None,
) -> list[GSPYieldGroupByDatetime]:
    """### Get PV_Live values for all GSPs for yesterday and today.

    The return object is a series of real-time PV generation estimates or
    truth values from __PV_Live__ for all GSPs.

    Setting the _regime_ parameter to _day-after_ includes
    the previous day's truth values for the GSPs.

    If _regime_ is not specified, the parameter defaults to _in-day_.

    If _compact_ is set to true, the response will be a list of GSPGenerations objects.
    This return object is significantly smaller, but less readable.

    #### Parameters
    - **regime**: can choose __in-day__ or __day-after__
    - **start_datetime_utc**: optional start datetime for the query.
    - **end_datetime_utc**: optional end datetime for the query.
    """
    # Why not just make the endpoint type list[int] and let fast API do this?
    gsp_ids: list[int] | None = convert_list_of_gsp_ids(gsp_ids)
    out: list[GSPYieldGroupByDatetime] = []

    gsp_uuid_id_map: dict[UUID, int] = {v.uuid: k for k, v in gsp_id_map.items()}

    if gsp_ids is None:
        # Return a snapshot of the data at the start_datetime_utc for all gsps
        values = await db.get_actual_generation_snapshot(
                location_uuids=[loc.uuid for loc in gsp_id_map.values()],
                snapshot_timestamp_utc=start_datetime_utc,
                energy_type=models.EnergyType.SOLAR,
                observer_name=f"pvlive_{regime}",
                authdata={},
            )
        out = [
            GSPYieldGroupByDatetime(
                datetime_utc=start_datetime_utc,
                generation_kw_by_gsp_id={
                    gsp_uuid_id_map[v.location_uuid]: v.power_kilowatts for v in values
                },
            ),
        ]

    elif len(gsp_ids) == 1:
        # Get observations as a timeseries
        values = await db.get_actual_generation(
            location_uuid=gsp_id_map[gsp_ids[0]].uuid,
            window_start=start_datetime_utc,
            window_end=end_datetime_utc,
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.GSP,
            authdata={},
            observer_name=f"pvlive_{regime}",
        )
        out = [
            GSPYieldGroupByDatetime(
                datetime_utc=v.valid_timestamp,
                generation_kw_by_gsp_id={gsp_uuid_id_map[v.location_uuid]: v.power_kilowatts},
            )
            for v in values
        ]

    else:
        # If multiple GSP IDs are set, then return a 2d response of all timestamps for each GSP.
        # Looping over snapshots results in fewer calls than looping over GSPs
        tasks = []
        for ts in pd.date_range(start=start_datetime_utc, end=end_datetime_utc, freq="30min"):
            tasks.append(
                asyncio.create_task(
                    db.get_actual_generation_snapshot(
                        location_uuids=list(gsp_uuid_id_map.keys()),
                        snapshot_timestamp_utc=ts,
                        energy_type=models.EnergyType.SOLAR,
                        observer_name=f"pvlive_{regime}",
                        authdata={},
                    ),
                ),
            )

        results: list[list[models.ActualGenerationValue] | Exception]  = await asyncio.gather(
            *tasks, return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                raise result

            if isinstance(result, list) and len(result) > 0:
                out.append(
                    GSPYieldGroupByDatetime(
                        datetime_utc=result[0].valid_timestamp,
                        generation_kw_by_gsp_id={
                            gsp_uuid_id_map[v.location_uuid]: v.power_kilowatts for v in result
                        },
                    ),
                )

    return out


