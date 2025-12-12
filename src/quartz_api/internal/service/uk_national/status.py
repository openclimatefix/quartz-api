"""The 'status' FastAPI router object."""


from fastapi import APIRouter
from starlette import status

from .pydantic_models import Status

router = APIRouter()


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
)
async def get_status(
) -> Status:
    """### Get status for the database and forecasts.

    Occasionally there may be a small problem or interruption with the forecast. This
    route is where the OCF team communicates the forecast status to users.
    """
    raise NotImplementedError()



@router.get("/check_last_forecast_run", include_in_schema=False)
async def check_last_forecast_run(
) -> None:
    """### Check the last forecast run status.

    This route is used to check the status of the last forecast run.
    """
    raise NotImplementedError()


@router.get("/update_last_data", include_in_schema=False)
async def update_last_data(
) -> None:
    """Update the last data. This is a legacy route, and should not be used."""
    raise NotImplementedError()
