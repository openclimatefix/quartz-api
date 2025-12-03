"""The 'substations' FastAPI router object and associated routes logic."""

import pathlib
from uuid import UUID

from fastapi import APIRouter, status

from quartz_api import internal
from quartz_api.internal.middleware.auth import AuthDependency

router = APIRouter(tags=[pathlib.Path(__file__).parent.stem.capitalize()])


@router.get(
    "/substations",
    status_code=status.HTTP_200_OK,
)
async def get_substations() -> list[str]:
    """Get substation groupings.

    Currently only primary substations are supported.
    """
    return ["primary"]


@router.get(
    "/substations/primary",
    status_code=status.HTTP_200_OK,
)
async def get_primary_substations(
    db: internal.DBClientDependency,
    auth: AuthDependency,
) -> list[internal.Site]:
    """Get all primary substations."""
    substations = await db.get_substations(authdata=auth)
    return substations

@router.get(
    "/substations/primary/{substation_uuid}",
    status_code=status.HTTP_200_OK,
)
async def get_primary_substation(
    substation_uuid: UUID,
    db: internal.DBClientDependency,
    auth: AuthDependency,
) -> internal.Site:
    """Get a primary substation by UUID."""
    substation = await db.get_location(
        location_uuid=substation_uuid,
        authdata=auth,
    )
    return substation

@router.get(
    "/substations/primary/{substation_uuid}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_substation_forecast(
    substation_uuid: UUID,
    db: internal.DBClientDependency,
    auth: AuthDependency,
) -> list[internal.PredictedPower]:
    """Get forecasted generation values of a primary substation."""
    forecast = await db.get_predicted_solar_power_production_for_location(
        location=substation_uuid,
        authdata=auth,
    )
    return forecast
