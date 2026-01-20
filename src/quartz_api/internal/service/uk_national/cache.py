"""Cache key builder."""
import logging
from collections.abc import Callable

from fastapi import Request, Response

log = logging.getLogger(__name__)

cache_dependent_scopes = ["read:intraday"]
legacy_query_params = ["compact", "historic"]


async def key_builder(
    func: Callable, # noqa
    namespace: str = "",
    *,
    request: Request = None,
    response: Response = None,   # noqa
    args,  # noqa
    kwargs,   # noqa
) -> str:
    """This makes a general cache key for the request.

    Note that different users will have the same cache
    We could have put this key builder in cmd/main.py
    but I thought it was too much of a risk to be used accidentally
    on private user routes
    """
    # only include cache dependent scopes
    # TODO fix for permissions of user, not scope of route
    scopes = request.scope.get("scopes", [])
    scopes = [scope for scope in scopes if scope in cache_dependent_scopes]

    params = request.query_params.items()
    # remove UI tag
    params = [(k, v) for k, v in params if k != "UI"]
    # remove some legacy query params that arent needed anymore
    params = [(k, v) for k, v in params if k not in legacy_query_params]

    key = ":".join([
        namespace,
        request.method.lower(),
        request.url.path,
        repr(sorted(params)),
        repr(sorted(scopes)),
    ])

    log.info(f"Cache key generated: {key}")

    return key
