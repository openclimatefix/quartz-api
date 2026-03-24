"""Cache key builder."""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi_cache.decorator import cache

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

log = logging.getLogger(__name__)

cache_dependent_scopes = ["read:intraday"]
legacy_query_params = ["historic"]


async def key_builder(
    func: Callable[..., Any],  # noqa: ARG001
    namespace: str = "",
    *,
    request: Request,
    response: Response,  # noqa: ARG001
    args: Any,  # noqa: ARG001, ANN401
    kwargs: Any, # noqa ANN401
) -> str:
    """This makes a general cache key for the request.

    Note that different users will have the same cache
    We could have put this key builder in cmd/main.py
    but I thought it was too much of a risk to be used accidentally
    on private user routes
    """
    # only include cache dependent scopes
    auth = kwargs.get("auth", {})
    permissions = auth.get("permissions", [])
    permissions = [p for p in permissions if p in cache_dependent_scopes]

    params = [
        (k, v) for k, v in request.query_params.items() if k not in [*legacy_query_params, "UI"]
    ]

    key = ":".join(
        [
            namespace,
            request.method.lower(),
            request.url.path,
            repr(sorted(params)),
            repr(sorted(permissions)),
        ],
    )

    log.info(f"Cache key generated: {key}")

    return key


async def get_gsps(
        db: models.StorageClientDependency, auth: AuthDependency) -> list[models.Location]:
    """Get all solar gsps and convert dict to pydantic models."""
    gsps = await get_gsps_cached(db, auth)
    if isinstance(gsps[0], dict):
        # the cache seems to return the list of pydantic elements as list of dicts
        gsps = [models.Location(**gsp) for gsp in gsps]

    return gsps


@cache(expire=300)
async def get_gsps_cached(
    db: models.StorageClientDependency, auth: AuthDependency,
) -> list[models.Location]:
    """Get all solar gsps."""
    gsps = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata=auth,
    )

    return gsps
