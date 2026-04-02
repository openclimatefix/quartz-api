"""Middleware to log API requests to the database."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from pyinstrument import Profiler
from starlette.middleware.base import BaseHTTPMiddleware


class ProfilerMiddleware(BaseHTTPMiddleware):
    """Middleware to profile API requests and log processing time."""

    def __init__(self, server: FastAPI) -> None:
        """Initialize the middleware with the FastAPI server."""
        super().__init__(server)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Profile the request processing time and log relevant information."""
        profiling = request.query_params.get("profile", False)
        if profiling:
            profiler = Profiler()
            profiler.start()
            await call_next(request)
            profiler.stop()
            return HTMLResponse(profiler.output_html())
        else:
            return await call_next(request)

