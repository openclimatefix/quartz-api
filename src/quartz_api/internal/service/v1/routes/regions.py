"""Region browsing routes — list and detail views for regions within a country."""

import asyncio

from fastapi import APIRouter, HTTPException, Query
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from ..endpoint_types import (
    CountryParam,
    OptionalValidRegionType,
    RegionDetail,
    ValidSource,
)
from ..helpers import (
    _check_country_access,
    _check_region_type,
    _location_to_detail,
    _resolve_nation,
    _resolve_region_id,
    _to_uuid,
)

router = APIRouter(tags=["Discovery"])


@router.get("/{country}/{source}/regions", status_code=status.HTTP_200_OK)
async def get_country_regions(
    source: ValidSource,
    country: CountryParam,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: OptionalValidRegionType = None,
    parent: str | None = Query(
        None,
        description="List children of a specific parent region (name or `national`).",
    ),
    name: str | None = Query(
        None,
        description="Filter by name (case-insensitive substring match).",
    ),
) -> list[RegionDetail]:
    """List regions for a country, optionally filtered by type, parent, or name.

    Returns `RegionDetail` objects containing each region's name, type,
    installed capacity, centroid, and any available metadata fields.

    Filter behaviour:

    - No filters — returns every region across all configured region types.
    - `region_type` — restricts results to one granularity level (e.g. `gsp`).
    - `parent` — returns the direct children of the specified parent region.
    - `name` — case-insensitive substring search across region names.

    Filters can be combined (e.g. `?region_type=gsp&name=london`).

    #### Parameters
    - **country**: country code (e.g. `GB`, `NL`).
    - **source**: energy source — currently only `solar` is supported.
    - **region_type**: optional region type slug (e.g. `national`, `gsp`).
      See `/{country}/{source}/region-types` for valid values.
    - **parent**: optional parent region identifier (name or `national`) — returns its
      children only.
    - **name**: optional name filter; returns regions whose name contains this string.
    """
    _check_country_access(auth, country)
    nation = await _resolve_nation(db, source, country, auth)

    if parent is not None:
        parent_uuid = await _resolve_region_id(parent, country, source, db)
        rt = _check_region_type(country, region_type, country.code)
        # Validate that parent is within the country, unless it IS the nation itself.
        if parent_uuid != nation.uuid:
            parent_location = await db.get_locations(
                energy_type=source,
                location_type=None,
                authdata={},
                location_uuid=parent_uuid,
                enclosing_location_uuid=_to_uuid(nation.uuid),
            )
            if len(parent_location) == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Parent region '{parent}' not found in {country.code}.",
                )
        locs = await db.get_locations(
            energy_type=source,
            location_type=rt.location_type if rt is not None else None,
            authdata={},
            enclosing_location_uuid=parent_uuid,
        )
        return _apply_name_filter(
            [_location_to_detail(loc, country) for loc in locs], name,
        )

    if region_type is not None:
        rt = _check_region_type(country, region_type, country.code)
        if rt.location_type == models.LocationType.NATION:
            return _apply_name_filter([_location_to_detail(nation, country)], name)

        locs = await db.get_locations(
            energy_type=source,
            location_type=rt.location_type,
            authdata={},
            enclosing_location_uuid=_to_uuid(nation.uuid),
        )
        return _apply_name_filter(
            [_location_to_detail(loc, country) for loc in locs], name,
        )

    # No filters — combine all region types
    tasks = []
    for rt in country.region_types:
        if rt.location_type == models.LocationType.NATION:
            continue
        tasks.append(
            db.get_locations(
                energy_type=source,
                location_type=rt.location_type,
                authdata={},
                enclosing_location_uuid=_to_uuid(nation.uuid),
            ),
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[RegionDetail] = [_location_to_detail(nation, country)]
    for result in results:
        if isinstance(result, Exception):
            raise result
        for loc in result:
            out.append(_location_to_detail(loc, country))
    return _apply_name_filter(out, name)


def _apply_name_filter(
    regions: list[RegionDetail], name: str | None,
) -> list[RegionDetail]:
    if name is None:
        return regions
    needle = name.lower()
    return [r for r in regions if needle in r.name.lower()]


@router.get("/{country}/{source}/regions/{region}", status_code=status.HTTP_200_OK)
async def get_region(
    source: ValidSource,
    country: CountryParam,
    region: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> RegionDetail:
    """Get details for a specific region.

    Returns a `RegionDetail` object with the region's name, type, installed
    capacity, centroid, and any available metadata fields.

    #### Parameters
    - **country**: country code (e.g. `GB`, `NL`).
    - **source**: energy source — currently only `solar` is supported.
    - **region**: region identifier — UUID, `national`, or region name
      (case-insensitive, e.g. `South West`).
    """
    _check_country_access(auth, country)
    resolved_id = await _resolve_region_id(region, country, source, db)

    locs = await db.get_locations(
        energy_type=source,
        location_type=None,
        authdata={},  # TODO: add auth when loosed on DP side
        location_uuid=resolved_id,
    )
    if len(locs) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Region '{resolved_id}' not found.",
        )
    return _location_to_detail(locs[0], country)
