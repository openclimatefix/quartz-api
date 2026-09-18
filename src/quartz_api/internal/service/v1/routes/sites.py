"""Site routes — list, create, read, and update sites for the caller's company."""

from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from ..endpoint_types import CountryParam, SiteInput, ValidSource
from ..helpers import check_country_access
from ..sites_scoping import (
    get_site,
    location_to_site_response,
    reject_not_updatable,
    require_org_id,
    site_input_to_metadata,
    validate_metadata_for_source,
)

router = APIRouter(tags=["Sites"])


@router.get(
    "/{country}/{source}/sites",
    status_code=status.HTTP_200_OK,
)
async def get_sites(
    country: CountryParam,
    source: ValidSource,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter by site status (e.g. 'active').",
    ),
    name: str | None = Query(
        None,
        description="Case-insensitive substring match on client_site_name.",
    ),
    min_latitude: float | None = Query(None, ge=-90, le=90),
    max_latitude: float | None = Query(None, ge=-90, le=90),
    min_longitude: float | None = Query(None, ge=-180, le=180),
    max_longitude: float | None = Query(None, ge=-180, le=180),
) -> dict:
    """List sites belonging to the caller's company."""
    check_country_access(auth, country)
    require_org_id(auth)

    # check if all coordinates are provided or none are provided
    coordinates_provided = [
        coordinate is not None
        for coordinate in (min_latitude, max_latitude, min_longitude, max_longitude)
    ]
    if any(coordinates_provided) and not all(coordinates_provided):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "min_latitude, max_latitude, min_longitude, and max_longitude "
                "must all be given together, or not at all."
            ),
        )

    # fetch sites
    locations = await db.get_locations(
        energy_type=source,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )

    sites = [location_to_site_response(location) for location in locations]

    # apply filters
    if status_filter is not None:
        sites = [site for site in sites if site["status"] == status_filter]

    if name is not None:
        needle = name.lower()
        sites = [site for site in sites if needle in (site["client_site_name"] or "").lower()]

    if all(coordinates_provided):
        sites = [
            site for site in sites
            if min_latitude <= site["latitude"] <= max_latitude
            and min_longitude <= site["longitude"] <= max_longitude
        ]

    # sort results
    sites.sort(
        key=lambda site: (
            site["client_site_name"] or "",
            str(site["site_id"]),
        ),
    )

    return {"sites": sites}


@router.post(
    "/{country}/{source}/sites",
    status_code=status.HTTP_201_CREATED,
)
async def create_site(
    country: CountryParam,
    source: ValidSource,
    site_input: SiteInput,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> dict:
    """Create a new site owned by the caller's company."""
    check_country_access(auth, country)
    owner_org_id = require_org_id(auth)

    if owner_org_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    validate_metadata_for_source(site_input, source)

    if site_input.latitude is None or site_input.longitude is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="latitude and longitude are required to create a site.",
        )
    if site_input.capacity_kW is None or site_input.capacity_kW <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="capacity_kW must be a positive number to create a site.",
        )

    # create site
    new_site_id = uuid4()
    location = models.Location(
        uuid=new_site_id,
        name=f"site_{new_site_id.hex}",
        latitude=site_input.latitude,
        longitude=site_input.longitude,
        capacity_kilowatts=site_input.capacity_kW,
        metadata=site_input_to_metadata(site_input),
    )

    created = await db.put_location(
        location=location,
        energy_type=source,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )

    await db.set_location_owner(
        location_uuid=created.uuid,
        organisation_id=owner_org_id,
        authdata=auth,
    )

    return location_to_site_response(created)


@router.get(
    "/{country}/{source}/sites/{site_id}",
    status_code=status.HTTP_200_OK,
)
async def get_site_detail(
    country: CountryParam,
    source: ValidSource,  # noqa: ARG001
    site_id: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> dict:
    """Get a single site's detail."""
    check_country_access(auth, country)

    # fetch site
    location = await get_site(db, site_id, auth)

    return location_to_site_response(location)


@router.put(
    "/{country}/{source}/sites/{site_id}",
    status_code=status.HTTP_200_OK,
)
async def update_site(
    country: CountryParam,
    source: ValidSource,
    site_id: UUID,
    site_input: SiteInput,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> dict:
    """Partially update a site. Fields left unset are unchanged."""
    check_country_access(auth, country)

    existing = await get_site(db, site_id, auth)
    validate_metadata_for_source(site_input, source)
    reject_not_updatable(site_input)

    # Nothing to save (e.g. {}, {"metadata": {}}, {"status": null}) — don't write a new version.
    new_metadata = site_input_to_metadata(site_input)
    if site_input.capacity_kW is None and not new_metadata:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update.",
        )

    # update site
    location = models.Location(
        uuid=site_id,
        name="", #name is ignored on update, so we can leave it blank
        latitude=existing.latitude,
        longitude=existing.longitude,
        capacity_kilowatts=(
            site_input.capacity_kW
            if site_input.capacity_kW is not None
            else existing.capacity_kilowatts
        ),
        metadata=new_metadata,
    )

    updated = await db.put_location(
        location=location,
        energy_type=source,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )

    return location_to_site_response(updated)
