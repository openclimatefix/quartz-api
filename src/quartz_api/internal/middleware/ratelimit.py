"""Rate limiting utilities for the Quartz API."""

import contextlib
import logging

import jwt
from fastapi import Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

log = logging.getLogger(__name__)


def get_user_key(request: Request) -> str:
    """Get a rate limit key that identifies the requesting user.

    Extracts the ``sub`` claim from the JWT bearer token in the Authorization
    header so that the limit is applied per authenticated user.  Falls back to
    the client IP address when no valid bearer token is present.

    Note: the JWT signature is **not** verified here because this function is
    only used to *identify* the caller for rate-limiting purposes, not to
    *authenticate* them.  The real authentication and signature verification is
    handled separately by the Auth0 middleware.  Using an unverified sub claim
    is acceptable here: a request with a forged token will still be rejected
    by the auth layer before any protected data is returned.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
        try:
            payload = jwt.decode(
                token,
                options={"verify_signature": False},
                algorithms=["RS256", "HS256"],
            )
            if sub := payload.get("sub"):
                return sub

        except jwt.PyJWTError:
            log.debug("Failed to decode JWT for rate limiting, falling back to IP")
    return get_remote_address(request)


default_limits = ["3600/hour", "20/second"]

limiter = Limiter(key_func=get_user_key, default_limits=default_limits, key_style="endpoint")


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer a rate limit with the same error shape as every other v1 error.

    slowapi's own handler returns `{"error": ...}`, where everything else in the API
    returns `{"detail": ...}`, so a client needs a special case for this one status.
    It also sends no `Retry-After`, leaving a caller to guess when to come back.
    """
    retry_after = 1
    limit = getattr(getattr(request.state, "view_rate_limit", None), "limit", None)
    window = getattr(limit, "get_expiry", None)
    if callable(window):
        with contextlib.suppress(Exception):
            retry_after = max(1, int(window()))

    detail = getattr(exc, "detail", "") or ""
    response = JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": f"Rate limit exceeded: {detail}. Applied per user per route."},
        headers={"Retry-After": str(retry_after)},
    )
    with contextlib.suppress(Exception):
        response = request.app.state.limiter._inject_headers(
            response, request.state.view_rate_limit,
        )
    return response
