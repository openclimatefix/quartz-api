"""Cache key builder."""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response

log = logging.getLogger(__name__)

cache_dependent_scopes = ["read:intraday"]
legacy_query_params = ["historic"]


async def key_builder(
    func: Callable[..., Any],
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

    # only store parameters that are in the function's signature
    params = [
        (k, v) for k, v in request.query_params.items() if k in list(func.__code__.co_varnames)
    ]

    key = ":".join(
        [
            namespace,
            request.method.lower(),
            request.url.path,
            repr(sorted(params)),
            repr(sorted(permissions)),
        ],
    ).replace("False", "false").replace("True", "true")

    log.info(f"Cache key generated: {key}")

    return key

