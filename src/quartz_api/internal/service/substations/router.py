"""The 'substations' FastAPI router object and associated routes logic."""

import pathlib
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

router = APIRouter(tags=[pathlib.Path(__file__).parent.stem.capitalize()])

@router.get(
    "/substations",
    status_code=status.HTTP_200_OK,
)
async def get_substations(
    request: Request,
    db: models.DBClientDependency,
    _: AuthDependency,
    substation_type: Literal["primary"] = "primary", # noqa: ARG001
) -> list[models.Substation]:
    """Get all substations.

    Note that currently only 'primary' substations are supported.
    """
    substations = await db.get_substations(authdata={}, traceid=request.state.trace_id)
    return substations

@router.get(
    "/substations/{substation_uuid}",
    status_code=status.HTTP_200_OK,
)
async def get_substation(
    request: Request,
    substation_uuid: UUID,
    db: models.DBClientDependency,
    _: AuthDependency,
) -> models.SubstationProperties:
    """Get a substation by UUID."""
    substation = await db.get_substation(
        location_uuid=substation_uuid,
        authdata={},
        traceid=request.state.trace_id,
    )
    return substation

@router.get(
    "/substations/{substation_uuid}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_substation_forecast(
    request: Request,
    substation_uuid: UUID,
    db: models.DBClientDependency,
    _: AuthDependency,
    tz: models.TZDependency,
) -> list[models.PredictedPower]:
    """Get forecasted generation values of a substation."""
    forecast = await db.get_substation_forecast(
        location_uuid=substation_uuid,
        authdata={},
        traceid=request.state.trace_id,
    )
    forecast = [
        value.to_timezone(tz=tz)
        for value in forecast
    ]
    return forecast

