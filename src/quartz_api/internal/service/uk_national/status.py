"""The 'status' FastAPI router object."""

import datetime as dt
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi_cache.decorator import cache
from sqlalchemy import create_engine, text
from starlette import status

from quartz_api.internal.models import EnergyType, LocationType, StorageClientDependency

from .cache import key_builder
from .endpoint_types import Status, gsp_id_map

router = APIRouter()


db_url = os.getenv("DB_URL", None)
if db_url is not None:
    engine = create_engine(db_url)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
)
async def get_status(request: Request) -> Status:  # noqa: ARG001
    """### Get status for the database and forecasts.

    Occasionally there may be a small problem or interruption with the forecast. This
    route is where the OCF team communicates the forecast status to users.
    """
    # Note that we want to upgrade this,
    # but currently this will pull from the nowcasting_datamodel database

    with engine.connect() as connection:
        result = connection.execute(text("SELECT * from status order by created_utc desc limit 1"))
        row = result.fetchone()
        status = Status(
            status=row[2],
            message=row[3],
        )
    return status


@router.get("/check_last_forecast_run", include_in_schema=False)
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
