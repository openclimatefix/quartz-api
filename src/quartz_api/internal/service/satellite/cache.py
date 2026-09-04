"""Cache key builder for the satellite API."""

from collections.abc import Callable
from typing import Any

from fastapi import Request, Response


async def key_builder(
    func: Callable[..., Any],
    namespace: str = "",
    *,
    request: Request,
    response: Response,  # noqa: ARG001
    args: Any,  # noqa: ARG001, ANN401
    kwargs: Any,  # noqa: ARG001, ANN401
) -> str:
    """Cache key builder for satellite routes.

    Keyed on query params only — responses (presigned URLs) don't vary per caller,
    so dependency-injected values like the S3 client and auth payload are excluded.
    """
    params = [
        (k, v)
        for k, v in request.query_params.items()
        if k in func.__code__.co_varnames
    ]
    return ":".join([namespace, request.method.lower(), request.url.path, repr(sorted(params))])
