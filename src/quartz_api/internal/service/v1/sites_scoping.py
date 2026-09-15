from uuid import UUID

from fastapi import HTTPException
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency, get_org_id_from_authdata

def require_org_id(auth: AuthDependency) -> str | None:
    """Return the org_id for the caller, or raise 403 if they have no company."""
    org_id = get_org_id_from_authdata(auth)
    if org_id == "no-org-access":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return org_id

async def get_site(
    db: models.StorageInterface,
    site_id: UUID,
    auth: AuthDependency,
) -> models.Location:
    """Return the site for the given site_id, or raise 404 if not found."""
    
    require_org_id(auth)
    locs = await db.get_locations(
        energy_type=None,
        location_type=models.LocationType.SITE,
        authdata=auth,
        location_uuid=site_id,
    )
    if not locs:
        raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No site found for '{site_id}'.",
        )
    return locs[0]
