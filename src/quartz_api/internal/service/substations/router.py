"""The 'substations' FastAPI router object and associated routes logic."""

import pathlib
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

router = APIRouter(tags=[pathlib.Path(__file__).parent.stem.capitalize()])

@router.get(
    "/substations",
    status_code=status.HTTP_200_OK,
)
async def get_substations(
    db: models.DBClientDependency,
    auth: AuthDependency,
    substation_type: Literal["primary"] = "primary", # noqa: ARG001
) -> list[models.Substation]:
    """Get all substations.

    Note that currently only 'primary' substations are supported.
    """
    substations = await db.get_substations(authdata=auth)
    return substations

@router.get(
    "/substations/{substation_uuid}",
    status_code=status.HTTP_200_OK,
)
async def get_substation(
    substation_uuid: UUID,
    db: models.DBClientDependency,
    auth: AuthDependency,
) -> models.SubstationProperties:
    """Get a substation by UUID."""
    substation = await db.get_substation(
        location_uuid=substation_uuid,
        authdata=auth,
    )
    return substation

@router.get(
    "/substations/{substation_uuid}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_substation_forecast(
    substation_uuid: UUID,
    db: models.DBClientDependency,
    auth: AuthDependency,
    tz: models.TZDependency,
) -> list[models.PredictedPower]:
    """Get forecasted generation values of a substation."""
    forecast = await db.get_substation_forecast(
        substation_uuid=substation_uuid,
        authdata=auth,
    )
    forecast = [
        value.to_timezone(tz=tz)
        for value in forecast
    ]
    return forecast

