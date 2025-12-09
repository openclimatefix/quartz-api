"""Middleware to log API requests to the database."""

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class TimerMiddleware(BaseHTTPMiddleware):
    """Middleware to log API requests to the database."""

    def __init__(self, server: FastAPI) -> None:
        """Initialize the middleware with the FastAPI server and database client."""
        super().__init__(server)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Log the time taken to process the request and proceed with the request."""
        start_time = time.time()
        response = await call_next(request)
        process_time = str(time.time() - start_time)

        logging.info(f"Process Time {process_time} {request.url}")
        response.headers["X-Process-Time"] = process_time

        return response
