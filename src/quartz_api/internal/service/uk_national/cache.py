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
    params = request.query_params.items()
    # remove UI tag
    params = [(k, v) for k, v in params if k != "UI"]
    # remove some legacy query params that arent needed anymore
    legacy_query_params = ["compact", "historic"]
    params = [(k, v) for k, v in params if k not in legacy_query_params]

    key = ":".join([
        namespace,
        request.method.lower(),
        request.url.path,
        repr(sorted(params)),
    ])

    return key
