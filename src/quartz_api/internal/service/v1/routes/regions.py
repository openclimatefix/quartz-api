"""Region browsing routes — list and detail views for regions within a country."""

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from ..cache import key_builder
from ..endpoint_types import (
    CountryParam,
    OptionalValidRegionType,
    RegionDetail,
    ValidRegion,
    ValidSource,
)
from ..helpers import (
    check_country_access,
    check_region_type,
    fetch_api_region,
    is_api_region,
    location_to_detail,
    resolve_nation,
    resolve_region_id,
    to_uuid,
)

router = APIRouter(tags=["Discovery"])


@router.get(
    "/{country}/{source}/regions",
    status_code=status.HTTP_200_OK,
    response_model=list[RegionDetail],
)
@cache(key_builder=key_builder, expire=60)
async def get_country_regions(
    request: Request,  # noqa: ARG001
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
    """List regions for a country, optionally filtered by type, parent, and/or name.

    Filter behavior:
    - No filters: returns every region across all configured region types.
    - `region_type`: restricts results to one granularity level (e.g. `gsp`).
    - `parent`: returns the direct children of the specified parent region.
    - `name`: case-insensitive substring search across region names.
    """
    check_country_access(auth, country)
    nation = await resolve_nation(db, source, country, auth)

    if parent is not None:
        parent_uuid = await resolve_region_id(parent, country, source, db)
        rt = check_region_type(country, region_type, country.code)
        # Validate that parent is within the country, unless it IS the nation itself.
        if parent_uuid != nation.uuid:
            parent_location = await db.get_locations(
                energy_type=source,
                location_type=None,
                authdata={},
                location_uuid=parent_uuid,
                enclosing_location_uuid=to_uuid(nation.uuid),
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
        # Without a region_type the platform returns every descendant, which includes
        # primary substations and individual sites as well as regions.
        return _filter_and_sort(
            [
                location_to_detail(loc, country)
                for loc in locs
                if is_api_region(loc, country)
            ],
            name,
            country,
        )

    if region_type is not None:
        rt = check_region_type(country, region_type, country.code)
        if rt.location_type == models.LocationType.NATION:
            return _filter_and_sort([location_to_detail(nation, country)], name, country)

        locs = await db.get_locations(
            energy_type=source,
            location_type=rt.location_type,
            authdata={},
            enclosing_location_uuid=to_uuid(nation.uuid),
        )
        return _filter_and_sort(
            [location_to_detail(loc, country) for loc in locs],
            name,
            country,
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
                enclosing_location_uuid=to_uuid(nation.uuid),
            ),
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[RegionDetail] = [location_to_detail(nation, country)]
    for result in results:
        if isinstance(result, Exception):
            raise result
        for loc in result:
            out.append(location_to_detail(loc, country))
    return _filter_and_sort(out, name, country)


# A location whose LocationType the country has no configured region type for is
# returned with `type: null` — it still needs somewhere to sort.
_UNTYPED_LEVEL = 10_000


def _filter_and_sort(
    regions: list[RegionDetail],
    name: str | None,
    country: CountryParam,
) -> list[RegionDetail]:
    """Apply the optional name filter, then impose a deterministic order.

    The data platform gives no ordering guarantee, so without this the same request can
    return the same regions in a different order each time the 60s cache expires.

    Ordered by region type level, then by name. National types are level 0, so they sort
    to the top without a special case.
    """
    if name is not None:
        needle = name.lower()
        regions = [r for r in regions if needle in r.name.lower()]

    levels = {rt.type: rt.level for rt in country.region_types}

    def _sort_key(region: RegionDetail) -> tuple[int, str]:
        level = _UNTYPED_LEVEL if region.type is None else levels[region.type]
        return (level, region.name.lower())

    return sorted(regions, key=_sort_key)


@router.get(
    "/{country}/{source}/regions/{region}",
    status_code=status.HTTP_200_OK,
    response_model=RegionDetail,
)
@cache(key_builder=key_builder, expire=60)
async def get_region(
    request: Request,  # noqa: ARG001
    source: ValidSource,
    country: CountryParam,
    region: ValidRegion,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> RegionDetail:
    """Get details for a specific region.

    Returns a `RegionDetail` object with the region's name, type, installed
    capacity, centroid, and any available metadata fields.
    """
    check_country_access(auth, country)
    resolved_id = await resolve_region_id(region, country, source, db)

    region = await fetch_api_region(resolved_id, country, source, db)
    return location_to_detail(region, country)
