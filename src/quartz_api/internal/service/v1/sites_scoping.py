"""This module contains functions for scoping sites to the caller's organization."""
from uuid import UUID

import sentry_sdk
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency, get_org_id_from_authdata

from .country_config import CountryConfig, SiteConfig
from .endpoint_types import (
    _NOT_UPDATABLE,
    BaseSiteMetadata,
    SiteInput,
    SolarMetadata,
    WindMetadata,
)

RESERVED_META_KEYS = frozenset(SiteInput.model_fields) - {"metadata"}
SOURCE_METADATA_CLASSES: dict[str, type[BaseSiteMetadata]] = {
    "solar": SolarMetadata,
    "wind": WindMetadata,
}


def validate_metadata_for_source(site_input: SiteInput, source: models.EnergyType) -> None:
    """Check metadata against the class for the URL's source, replacing it in place."""
    if site_input.metadata is None:
        return
    meta_cls = SOURCE_METADATA_CLASSES[source.name.lower()]
    try:
        site_input.metadata = meta_cls.model_validate(
            site_input.metadata.model_dump(exclude_unset=True),
        )
    except ValidationError as e:
        raise RequestValidationError(
            [
                {**err, "loc": ("body", "metadata", *err["loc"])}
                for err in e.errors(include_url=False)
            ],
        ) from e


def reject_not_updatable(site_input: SiteInput) -> None:
    """Raise 400 if a PUT body sends any field flagged _NOT_UPDATABLE (create-only)."""
    sent = [
        f"{prefix}{name}"
        for prefix, model in (("", site_input), ("metadata.", site_input.metadata))
        if model is not None
        for name in model.model_fields_set
        if name in type(model).model_fields
        and type(model).model_fields[name].json_schema_extra == _NOT_UPDATABLE
    ]
    if sent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{', '.join(sorted(sent))} cannot be changed after a site is created.",
        )


def require_org_id(auth: AuthDependency) -> str | None:
    """Return the org_id for the caller, or raise 403 if they have no company."""
    org_id = get_org_id_from_authdata(auth)
    if org_id == "no-org-access":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    #ocf admin returns None but have a valid org_id, added below for testing
    if org_id is None:
        app_metadata = auth.get("app_metadata", {})
        org_id = app_metadata.get("hubspot_company_id") if isinstance(app_metadata, dict) else None
    return org_id

async def get_site(
    db: models.StorageInterface,
    site_id: UUID,
    auth: AuthDependency,
    energy_type: models.EnergyType | None = None,
) -> models.Location:
    """Return the site for the given site_id, or raise 404 if not found."""
    require_org_id(auth)
    locs = await db.get_locations(
        energy_type=energy_type,
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

def location_to_site_response(loc: models.Location) -> dict:
    """Convert an internal Location to the final site response."""
    if loc.energy_type is None:
        sentry_sdk.capture_message(f"Site {loc.uuid} has no energy_type set.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Site '{loc.uuid}' has no energy_type set. Check DB.",
        )

    source = loc.energy_type.name.lower()
    meta_cls = SOURCE_METADATA_CLASSES.get(source)

    if meta_cls is None:
        sentry_sdk.capture_message(
            f"Site {loc.uuid} has unsupported energy_type '{source}'.",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Site '{loc.uuid}' has unsupported energy_type '{source}'. "
                "Check the EnergyType enum in the DB interface. "
                "It might be a recently added energy type."
            ),
        )

    metadata = dict(loc.metadata)

    client_site_id = metadata.get("client_site_id")
    if isinstance(client_site_id, float) and client_site_id.is_integer():
        client_site_id = int(client_site_id)

    known_fields = set(meta_cls.model_fields)

    promoted = {
        key: value
        for key, value in metadata.items()
        if key in known_fields
    }

    leftover = {
        key: value
        for key, value in metadata.items()
        if key not in known_fields and key not in RESERVED_META_KEYS
    }

    return {
        "site_id": loc.uuid,
        "capacity_kW": loc.capacity_kilowatts,
        "client_site_id": client_site_id,
        "client_site_name": metadata.get("client_site_name", loc.name),
        "status": metadata.get("status", "active"),
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        **promoted,
        "metadata": leftover,
    }

def site_input_to_metadata(site_input: SiteInput) -> dict[str, str | int | float]:
    """Build a metadata dict from only the fields a caller actually set."""
    metadata: dict[str, str | int | float] = {}
    for field_name in RESERVED_META_KEYS:
        # These have their own columns on the location, not keys in metadata.
        if field_name in ("latitude", "longitude", "capacity_kW"):
            continue
        value = getattr(site_input, field_name)
        if value is not None:
            metadata[field_name] = value
    if site_input.metadata is not None:
        metadata.update(
            {
                k: v
                for k, v in site_input.metadata.model_dump(exclude_none=True).items()
                if k not in RESERVED_META_KEYS
            },
        )
    return metadata


def site_config_for(country: CountryConfig, source: models.EnergyType) -> SiteConfig:
    """Return the country's site config for this source, or 404 if it has none."""
    site_cfg = country.get_site_config(source.name.lower())
    if site_cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sites are not available for {country.code}/{source.name.lower()}.",
        )
    return site_cfg


def resolve_site_forecaster(site: models.Location, site_cfg: SiteConfig) -> str:
    """Return the forecaster to read: the site's own override, else the country default."""
    forecaster = site.metadata.get("forecast_name") or site_cfg.default_forecaster_name
    if not forecaster:
        sentry_sdk.capture_message(
            f"No forecaster for site {site.uuid}: no metadata['forecast_name'] and no "
            f"default_forecaster_name in config. check the country_config and add one.",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No forecast model is configured for this site.",
        )
    return str(forecaster)
