"""Region browsing routes — list and detail views for regions within a country."""

# ruff: noqa: ARG001, B008

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from ..endpoint_types import RegionDetail, ValidRegionType, ValidSource
from ..helpers import (
    CountryCode,
    _check_region_type,
    _country_config,
    _energy_type_for,
    _location_to_detail,
    _resolve_nation,
    _resolve_region_id,
    _to_uuid,
)

router = APIRouter(tags=["Discovery"])


@router.get("/{country}/{source}/regions", status_code=status.HTTP_200_OK)
async def get_country_regions(
    source: ValidSource,
    country: CountryCode,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: ValidRegionType | None = None,
    parent_id: UUID | None = Query(
        None,
        description="List children of a specific region.",
    ),
) -> list[RegionDetail]:
    """List regions for a country, optionally filtered by type or parent.

    - No filters: returns all regions of all configured types.
    - ``?region_type=gsp``: only GSP regions.
    - ``?parent_id={uuid}``: children of a specific region.
    """
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
    nation = await _resolve_nation(db, energy_type, cfg, auth)

    if parent_id is not None:
        rt = _check_region_type(cfg, region_type, country)
        # Validate that parent_id is within the country, unless it IS the nation itself.
        if parent_id != nation.uuid:
            parent_location = await db.get_locations(
                energy_type=energy_type,
                location_type=None,
                authdata={},
                location_uuid=parent_id,
                enclosing_location_uuid=_to_uuid(nation.uuid),
            )
            if len(parent_location) == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Parent region with UUID '{parent_id}' not found in {country.upper()}.",
                )
        locs = await db.get_locations(
            energy_type=energy_type,
            location_type=rt.location_type if rt is not None else None,
            authdata={},
            enclosing_location_uuid=parent_id,
        )
        return [_location_to_detail(loc, cfg) for loc in locs]

    if region_type is not None:
        rt = _check_region_type(cfg, region_type, country)
        if rt.location_type == models.LocationType.NATION:
            return [_location_to_detail(nation, cfg)]

        locs = await db.get_locations(
            energy_type=energy_type,
            location_type=rt.location_type,
            authdata={},
            enclosing_location_uuid=_to_uuid(nation.uuid),
        )
        return [_location_to_detail(loc, cfg) for loc in locs]

    # No filters — combine all region types
    tasks = []
    for rt in cfg.region_types:
        if rt.location_type == models.LocationType.NATION:
            continue
        tasks.append(
            db.get_locations(
                energy_type=energy_type,
                location_type=rt.location_type,
                authdata={},
                enclosing_location_uuid=_to_uuid(nation.uuid),
            ),
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[RegionDetail] = [_location_to_detail(nation, cfg)]
    for result in results:
        if isinstance(result, Exception):
            raise result
        for loc in result:
            out.append(_location_to_detail(loc, cfg))
    return out


@router.get("/{country}/{source}/regions/{region_id}", status_code=status.HTTP_200_OK)
async def get_region(
    source: ValidSource,
    country: CountryCode,
    region_id: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> RegionDetail:
    """Get details for a specific region."""
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
    resolved_id = await _resolve_region_id(region_id, cfg, energy_type, db)

    locs = await db.get_locations(
        energy_type=energy_type,
        location_type=None,
        authdata={},  # TODO: add auth when loosed on DP side
        location_uuid=resolved_id,
    )
    if len(locs) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Region '{resolved_id}' not found.",
        )
    return _location_to_detail(locs[0], cfg)
