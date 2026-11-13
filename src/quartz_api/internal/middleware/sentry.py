"""Middleware to add user details to sentry for error tracking."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import sentry_sdk
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from quartz_api.internal.middleware import auth
from quartz_api.internal.middleware.auth import get_org_id_from_authdata

# Requests that carry no useful performance data. Health checks alone come from the
# container HEALTHCHECK and the load balancer every few seconds, all day.
_UNTRACED_PATHS = frozenset({"/health", "/favicon.ico", "/docs", "/openapi.json"})
_UNTRACED_PREFIXES = ("/static/",)


def make_traces_sampler(rate: float) -> Callable[[dict[str, Any]], float]:
    """Build a Sentry traces_sampler that skips noise and samples the rest at `rate`."""

    def traces_sampler(sampling_context: dict[str, Any]) -> float:
        scope = sampling_context.get("asgi_scope") or {}
        path: str = scope.get("path", "")
        if scope.get("method") == "OPTIONS":
            return 0.0
        # Mounted sub-apps (e.g. /v1) serve their own docs and schema.
        tail = "/" + path.rstrip("/").rsplit("/", 1)[-1]
        if path in _UNTRACED_PATHS or tail in _UNTRACED_PATHS or path.startswith(
            _UNTRACED_PREFIXES,
        ):
            return 0.0
        # Keep a trace whole when an upstream service already decided to sample it.
        parent = sampling_context.get("parent_sampled")
        if parent is not None:
            return float(parent)
        return rate

    return traces_sampler


class SentryUserMiddleware(BaseHTTPMiddleware):
    """add user details to sentry for HTTP requests."""

    def __init__(
        self,
        app: ASGIApp,
        auth_instance: auth.AuthClient | None,
    ) -> None:
        """Initialize the middleware and auth instance.

        `add_middleware` passes the wrapped ASGI app, not the FastAPI instance.
        """
        super().__init__(app)
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
