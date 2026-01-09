"""Cache key builder."""
from collections.abc import Callable

from fastapi import Request, Response


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
    key = ":".join([
        namespace,
        request.method.lower(),
        request.url.path,
        repr(sorted(request.query_params.items())),
    ])
    return key
