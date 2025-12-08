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


# TODO /check_last_forecast_run
# TODO /update_last_data
