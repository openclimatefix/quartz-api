"""The 'status' FastAPI router object."""

import os
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi_cache.decorator import cache
from sqlalchemy import create_engine, text
from starlette import status

from quartz_api.internal.models import (
    DBClientDependency,
)

from .cache import key_builder
from .pydantic_models import Status
from .time_utils import floor_30_minutes_dt

router = APIRouter()


db_url = os.getenv("DB_URL", None)
if db_url is not None:
    engine = create_engine(db_url)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
)
async def get_status() -> Status:
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
    db: DBClientDependency,
    model_name: str | None = None) -> datetime:
    """### Check the last forecast run status.

    This route is used to check the status of the last forecast run.
    """
    sites = await db.get_solar_regions(type="nation")
    national_location_uuid = sites[0].region_metadata["location_uuid"]

    datetime_now_utc = datetime.now(tz=datetime.now(tz=UTC))
    start_datetime = floor_30_minutes_dt(datetime_now_utc)

    # Get the national forecast,
    # but just get it for one datestamp (to make it quick)
    predict_powers = await db.get_predicted_solar_power_production_for_location(
        location_uuid=national_location_uuid,
        authdata={},
        model_name=model_name,
        start_datetime=start_datetime,
        end_datetime=start_datetime,
    )

    return predict_powers[0].created_time



@router.get("/update_last_data", include_in_schema=False)
async def update_last_data() -> None:
    """Update the last data. This is a legacy route, and should not be used."""
    raise NotImplementedError()
