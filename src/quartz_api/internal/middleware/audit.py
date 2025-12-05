"""Middleware to log API requests to the database."""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from quartz_api.internal import models


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """Middleware to log API requests to the database."""

    def __init__(self, server: FastAPI) -> None:
        """Initialize the middleware with the FastAPI server and database client."""
        super().__init__(server)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Log the API request to the database and proceed with the request."""
        response = await call_next(request)

        # Skip OPTIONS requests
        if request.method == "OPTIONS":
            return response

        auth = getattr(request.state, "auth", {})

        logging.debug("Referer: %s", request.headers.get("referer"))

        url = request.url.path
        if request.url.query:
             url += f"?{request.url.query}"

        try:
            db_client: models.DatabaseInterface = getattr(request.app.state, "db_instance", None)
            if db_client is None:
                raise RuntimeError("Database client not found in app state.")
            await self.db_client.save_api_call_to_db(url=url, authdata=auth)
        except Exception as e:
            logging.error(f"Failed to log request to DB: {e}")

        return response
