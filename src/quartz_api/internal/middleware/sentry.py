"""Middleware to add user details to sentry for error tracking."""

import logging
from collections.abc import Awaitable, Callable

import sentry_sdk
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from quartz_api.internal.middleware import auth
from quartz_api.internal.middleware.auth import get_org_id_from_authdata


class SentryUserMiddleware(BaseHTTPMiddleware):
    """add user details to sentry for HTTP requests."""

    def __init__(
        self,
        server: FastAPI,
        auth_instance: auth.AuthClient | None,
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
            self.auth_instance,
            auth.DummyBackend,
        ):
            try:
                payload = await self.auth_instance.require_auth()(request)
                if payload:
                    sentry_sdk.set_user(
                        {
                            "id": payload.get("sub"),
                            "email": payload.get(auth.EMAIL_KEY),
                        },
                    )
                    org_id = get_org_id_from_authdata(payload)
                    if org_id:
                        sentry_sdk.set_tag("hubspot_company_id", org_id)
            except Exception:
                # silently fail to not break requests
                logging.debug("Could not extract user for Sentry")

        response = await call_next(request)
        return response
