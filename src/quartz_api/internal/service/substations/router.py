"""The 'substations' FastAPI router object and associated routes logic."""

import datetime as dt
import pathlib
from typing import Annotated, Literal
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from .endpoint_types import (
    OneDatetimeManyForecastValues,
    PredictedPower,
    Substation,
    SubstationProperties,
)

router = APIRouter(tags=[pathlib.Path(__file__).parent.stem.capitalize()])


@router.get(
    "/substations",
    status_code=status.HTTP_200_OK,
)
async def get_substations(
    db: models.StorageClientDependency,
    _: AuthDependency,
    substation_type: Literal["primary"] = "primary",
) -> list[Substation]:
    """Get all substations.

    Note that currently only 'primary' substations are supported.
    """
    locs = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.SUBSTATION,
        authdata={},
    )
    out: list[Substation] = [
        Substation(
            substation_uuid=loc.uuid,
            substation_name=loc.name,
            substation_type=str(substation_type),
            latitude=loc.latitude,
            longitude=loc.longitude,
            capacity_kW=loc.capacity_kilowatts,
            metadata=loc.metadata,
        )
        for loc in locs
    ]
    return out


@router.get(
    "/substations/{substation_uuid:uuid}",
    status_code=status.HTTP_200_OK,
)
async def get_substation(
    substation_uuid: UUID,
    db: models.StorageClientDependency,
    _: AuthDependency,
) -> SubstationProperties:
    """Get a substation by UUID."""
    locs = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_uuid=substation_uuid,
        location_type=models.LocationType.SUBSTATION,
        authdata={},
    )
    if len(locs) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Substation with UUID {substation_uuid} not found.",
        )
    loc = locs[0]
    return SubstationProperties(
        substation_name=loc.name,
        substation_type="primary",
        latitude=loc.latitude,
        longitude=loc.longitude,
        capacity_kW=loc.capacity_kilowatts,
        metadata=loc.metadata,
    )


@router.get(
    "/substations/{substation_uuid:uuid}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_substation_forecast(
    substation_uuid: UUID,
    db: models.StorageClientDependency,
    _: AuthDependency,
    tz: models.TZDependency,
) -> list[PredictedPower]:
    """Get forecasted generation values of a substation."""
    forecast = await db.get_predicted_generation(
        location_uuid=substation_uuid,
        window_start=pd.Timestamp.utcnow().floor("6h").to_pydatetime() - dt.timedelta(days=2),
        window_end=pd.Timestamp.utcnow().floor("6h").to_pydatetime() + dt.timedelta(days=2),
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.SUBSTATION,
        forecaster_name="blend",
        authdata={},
    )

    out: list[PredictedPower] = [
        PredictedPower(
            power_kW=v.power_kilowatts,
            time=v.valid_timestamp.astimezone(tz=tz),
            created_time=v.created_timestamp.astimezone(tz=tz),
            initialization_timestamp_utc=v.init_timestamp.astimezone(tz=tz),
            forecaster_name=v.forecaster_name,
            forecaster_version=v.forecaster_version,
            plevel_kW=v.plevels_kilowatts,
        )
        for v in forecast
    ]

    return out


@router.get(
    "/substations/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_all_substation_forecast_at_one_timestamp(
    db: models.StorageClientDependency,
    _: AuthDependency,
    datetime_utc: Annotated[
        dt.datetime,
        Query(default_factory=lambda: pd.Timestamp.utcnow().floor("30min").to_pydatetime()),
    ],
) -> OneDatetimeManyForecastValues:
    """Get forecasted generation values of all substations at a specific timestamp."""
    # Fetch all the substations
    substations = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.SUBSTATION,
        authdata={},
    )
    gsp_ids = sorted({loc.metadata["gsp_id"] for loc in substations})

    # Fetch all the GSPs and filter to those relevant to substations
    gsps = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata={},
    )
    gsp_uuids = [loc.uuid for loc in gsps if loc.metadata["gsp_id"] in gsp_ids]
    # Get a mapping of substations uuids to gsp uuids and capacities
    substations_to_gsp_map: dict[UUID, dict[str, UUID | float]] = {
        substation.uuid: {
            "uuid": next(
                gsp.uuid for gsp in gsps if gsp.metadata["gsp_id"] == substation.metadata["gsp_id"]
            ),
            "capacity_kilowatts": next(
                gsp.capacity_kilowatts
                for gsp in gsps
                if gsp.metadata["gsp_id"] == substation.metadata["gsp_id"]
            ),
        }
        for substation in substations
    }

    # Fetch the forecast snapshot for the gsps
    snapshot = await db.get_predicted_generation_snapshot(
        location_uuids=gsp_uuids,
        snapshot_timestamp_utc=datetime_utc,
        energy_type=models.EnergyType.SOLAR,
        forecaster_name="blend",
        authdata={},
    )
    snapshot_dict: dict[UUID, models.PredictedGenerationValue] = {
        pgv.location_uuid: pgv for pgv in snapshot
    }

    # Make substation output based on gsp values
    out_dict: dict[str, float] = {
        str(s.uuid): snapshot_dict[substations_to_gsp_map[s.uuid]["uuid"]].power_kilowatts
        * (s.capacity_kilowatts / substations_to_gsp_map[s.uuid]["capacity_kilowatts"])
        for s in substations
    }

    out = OneDatetimeManyForecastValues(
        datetime_utc=datetime_utc,
        forecast_values_kW=out_dict,
    )

    return out
