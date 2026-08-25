"""The 'status' FastAPI router object."""

import datetime as dt
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal.models import EnergyType, LocationType, StorageClientDependency

from .cache import key_builder
from .endpoint_types import Status, gsp_id_map

log = logging.getLogger(__name__)

router = APIRouter()
router_check_last_forecast_run = APIRouter()

# Interim: status is proxied from the Quartz Status API until clients are moved
# onto it directly, at which point this route can be retired.
# Products are "gb-solar", "nl-solar" and "asset-solar" (sites/assets);
# only GB has a v0 route, so the product key is fixed here rather than configured.
STATUS_URL = os.getenv("STATUS_API_URL", "https://status.quartz.energy")
STATUS_PRODUCT = "gb-solar"


@router.get(
    "",
    response_model=Status,
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder, expire=10)
async def get_status(request: Request) -> Status:  # noqa: ARG001
    """### Get status for the database and forecasts.

    Occasionally there may be a small problem or interruption with the forecast. This
    route is where the OCF team communicates the forecast status to users.

    Status is sourced from the Quartz Status API. New integrations should query
    https://status.quartz.energy/products/gb-solar directly — this route is a
    temporary proxy and will be retired.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{STATUS_URL}/products/{STATUS_PRODUCT}")
            response.raise_for_status()
            payload = response.json()
        return Status(status=payload["status"], message=payload.get("message") or "")
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        log.exception(
            "Could not fetch status for %s from %s",
            STATUS_PRODUCT,
            STATUS_URL,
        )
        return Status(
            status="unknown",
            message="Status information is currently unavailable.",
        )


@router_check_last_forecast_run.get("/check_last_forecast_run", include_in_schema=False)
@cache(key_builder=key_builder)
async def check_last_forecast_run(
    request: Request,  # noqa: ARG001
    db: StorageClientDependency, model_name: str | None = "blend_adjust",
) -> dt.datetime:
    """### Check the last forecast run status.

    This route is used to check the status of the last forecast run.
    """
    if 0 not in gsp_id_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location not found",
        )

    # Get the national forecast,
    # but just get it for one datestamp (to make it quick)
    forecast = await db.get_predicted_generation(
        location_uuid=gsp_id_map[0].uuid,
        location_type=LocationType.NATION,
        energy_type=EnergyType.SOLAR,
        window_start=dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=30),
        window_end=dt.datetime.now(tz=dt.UTC),
        authdata={},
        forecaster_name=model_name,
    )

    return forecast[0].created_timestamp


@router.get("/update_last_data", include_in_schema=False)
async def update_last_data(request: Request) -> None:
    """Update the last data. This is a legacy route, and should not be used."""
    raise NotImplementedError()
