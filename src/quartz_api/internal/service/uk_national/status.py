"""The 'status' FastAPI router object."""

from datetime import datetime

from fastapi import APIRouter
from starlette import status

from quartz_api.internal.models import (
    DBClientDependency,
)

from .pydantic_models import Status

router = APIRouter()


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
)
async def get_status() -> Status:
    """### Get status for the database and forecasts.

    Occasionally there may be a small problem or interruption with the forecast. This
    route is where the OCF team communicates the forecast status to users.
    """
    raise NotImplementedError()


@router.get("/check_last_forecast_run", include_in_schema=False)
async def check_last_forecast_run(
    db: DBClientDependency,
    model_name: str | None = None) -> datetime:
    """### Check the last forecast run status.

    This route is used to check the status of the last forecast run.
    """
    sites = await db.get_solar_regions(type="nation")
    national_location_uuid = sites[0].region_metadata["location_uuid"]

    forecast = await db.get_forecast_metadata(
        location_uuid=national_location_uuid,
        authdata={},
        model_name=model_name,
    )

    # we should use created_timestamp_utc,
    # but currently thats deafulted to 1970-01-01
    # So for now we use initialization_timestamp_utc
    return forecast.initialization_timestamp_utc



@router.get("/update_last_data", include_in_schema=False)
async def update_last_data() -> None:
    """Update the last data. This is a legacy route, and should not be used."""
    raise NotImplementedError()
