"""Middleware to log API requests to the database."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

CORR_HEADER = "X-Request-Id"
PROC_TIME_HEADER = "X-Process-Time"


class TracerMiddleware(BaseHTTPMiddleware):
    """Middleware to add tracing information to API requests."""

    def __init__(self, server: FastAPI) -> None:
        """Initialize the middleware with the FastAPI server and database client."""
        super().__init__(server)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Enrich the request with tracing information and log relevant information."""
        start_time = time.time()
        trace_id: str = request.headers.get(CORR_HEADER, uuid.uuid4().hex)

        # Log the start of the request processing and enrich request state
        logging.info(f"Started request {request.url}", extra={"trace_id": trace_id})
        request.state.trace_id = trace_id

        # Process the request
        response = await call_next(request)

        # Log the end of the request processing and enrich response headers
        process_time = str(time.time() - start_time)
        logging.info(
            f"Finished request {request.url}",
            extra={"trace_id": trace_id, "process_time": process_time},
        )
        response.headers[PROC_TIME_HEADER] = process_time
        response.headers[CORR_HEADER] = trace_id

        return response
