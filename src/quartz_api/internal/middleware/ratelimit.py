"""Rate limiting utilities for the Quartz API."""

import logging

import jwt
from fastapi import Request
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

