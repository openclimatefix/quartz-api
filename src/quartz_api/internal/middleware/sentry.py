"""Middleware to add user details to sentry for error tracking."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware

from quartz_api.internal.middleware import auth

log = logging.getLogger(__name__)


class SentryUserMiddleware(BaseHTTPMiddleware):
    """add user details to sentry for HTTP requests."""

    def __init__(
        self,
        server: FastAPI,
        auth_instance: auth.Auth0 | auth.DummyAuth,
    ) -> None:
        """Initialize FastAPI server and auth instance."""
        super().__init__(server)
        self.auth_instance = auth_instance

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Add user details to a context before processing request."""
        if self.auth_instance is not None and not isinstance(
            self.auth_instance, auth.DummyAuth,
        ):
            try:
                authorization = request.headers.get("Authorization", "")
                if authorization.startswith("Bearer "):
                    token = authorization.replace("Bearer ", "")
                    credentials = HTTPAuthorizationCredentials(
                        scheme="Bearer", credentials=token,
                    )
                    payload = self.auth_instance(request, credentials)
                    if payload:
                        import sentry_sdk

                        sentry_sdk.set_user({
                            "id": payload.get("sub"),
                            "email": payload.get(auth.EMAIL_KEY),
                        })
            except Exception:
                # silently fail to not break requests
                log.debug("Could not extract user for Sentry")

        response = await call_next(request)
        return response

