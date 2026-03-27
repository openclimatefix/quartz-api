"""API providing access to OCF's Quartz Forecasts."""

import asyncio
import functools
import importlib
import importlib.metadata
import logging
import os
import pathlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import sentry_sdk
from apitally.fastapi import ApitallyMiddleware
from dp_sdk.ocf import dp
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from grpclib.client import Channel
from pydantic import BaseModel
from pyhocon import ConfigFactory, ConfigTree
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from quartz_api.internal import models, service
from quartz_api.internal.backends import (
    DataPlatformStorage,
    DummyStorage,
    EnrichedChannel,
    QuartzStorage,
)
from quartz_api.internal.middleware import audit, auth, ratelimit, sentry, trace
from quartz_api.internal.service.uk_national.endpoint_types import gsp_id_map

from ._logging import setup_json_logging

if TYPE_CHECKING:
    from grpclib.client import Channel

log = logging.getLogger(__name__)
logging.getLogger("hpack").setLevel(logging.WARNING)

static_dir = pathlib.Path(__file__).parent.parent / "static"


class GetHealthResponse(BaseModel):
    """Model for the health endpoint response."""

    status: int


def _custom_openapi(server: FastAPI) -> dict[str, Any]:
    """Customize the OpenAPI schema for ReDoc."""
    if server.openapi_schema:
        return server.openapi_schema

    openapi_schema = get_openapi(
        title=server.title,
        version=server.version,
        description=server.description,
        contact={
            "name": "Quartz API by Open Climate Fix",
            "url": "https://www.quartz.solar",
            "email": "info@openclimatefix.org",
        },
        license_info={
            "name": "MIT License",
            "url": "https://github.com/openclimatefix/quartz-api/blob/main/LICENSE",
        },
        routes=server.routes,
    )

    openapi_schema["info"]["x-logo"] = {"url": "/static/logo.png"}
    openapi_schema["tags"] = server.openapi_tags
    server.openapi_schema = openapi_schema

    return openapi_schema


@asynccontextmanager
async def _lifespan(server: FastAPI, conf: ConfigTree) -> AsyncGenerator[None]:
    """Configure FastAPI app instance with startup and shutdown events."""
    storage: models.StorageInterface | None = None
    grpc_channel: Channel | None = None

    match conf.get_string("backend.source"):
        case "quartzdb":
            storage = QuartzStorage(
                database_url=conf.get_string("backend.quartzdb.database_url"),
            )
        case "dummydb":
            storage = DummyStorage()
            log.warning("disabled backend. NOT recommended for production")
        case "dataplatform":
            grpc_channel = EnrichedChannel(
                host=conf.get_string("backend.dataplatform.host"),
                port=conf.get_int("backend.dataplatform.port"),
            )
            client = dp.DataPlatformDataServiceStub(channel=grpc_channel)
            storage = DataPlatformStorage.from_dp(dp_client=client)
            storage.set_sync_client(
                host=conf.get_string("backend.dataplatform.host"),
                port=conf.get_int("backend.dataplatform.port"),
            )

            if "uk_national" in conf.get_string("api.routers").split(","):
                # Populate the GSP ID to UUID mapping
                resp = await storage.get_locations(
                        location_type=models.LocationType.GSP,
                        energy_type=models.EnergyType.SOLAR,
                        authdata={},
                    )
                resp += await storage.get_locations(
                        location_type=models.LocationType.NATION,
                        energy_type=models.EnergyType.SOLAR,
                        authdata={},
                )
                for loc in resp:
                    if "gsp_id" in loc.metadata:
                        gsp_id_map[int(loc.metadata["gsp_id"])] = loc
                log.info(f"Populated GSP ID map with {len(gsp_id_map)} entries")

        case _ as backend_type:
            raise ValueError(f"Unknown backend: {backend_type}")

    server.dependency_overrides[models.get_storage_client] = lambda: storage

    warm_task = None
    if "uk_national" in conf.get_string("api.routers"):
        from quartz_api.internal.service.uk_national.gsp_router import _warm_forecast_all_cache
        warm_task = asyncio.create_task(_warm_forecast_all_cache(server))

    yield

    if warm_task is not None:
        warm_task.cancel()

    gsp_id_map.clear()
    if grpc_channel:
        grpc_channel.close()


def _create_server(conf: ConfigTree) -> FastAPI:
    """Configure FastAPI app instance with routes, dependencies, and middleware."""
    setup_json_logging(level=logging.getLevelName(conf.get_string("api.loglevel").upper()))
    description = "API providing access to OCF's Quartz Forecasts."
    server = FastAPI(
        version=importlib.metadata.version("quartz_api"),
        lifespan=functools.partial(_lifespan, conf=conf),
        title="Quartz API",
        openapi_tags=[
            {
                "name": "API Information",
                "description": "Routes providing information about the API.",
            },
        ],
        docs_url="/swagger",
        redoc_url=None,
        swagger_ui_init_oauth={
            "usePkceWithAuthorizationCodeGrant": True,
        },
    )

    FastAPICache.init(InMemoryBackend(), expire=120, prefix="fastapi-cache")

    # Register rate limiter
    server.state.limiter = ratelimit.limiter
    server.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    server.add_middleware(SlowAPIMiddleware)

    # Add the default routes
    server.mount("/static", StaticFiles(directory=static_dir.as_posix()), name="static")

    @server.get("/health", tags=["API Information"], status_code=status.HTTP_200_OK)
    def get_health_route() -> GetHealthResponse:
        """Health endpoint for the API."""
        return GetHealthResponse(status=status.HTTP_200_OK)

    @server.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        """Serve the favicon."""
        return FileResponse(static_dir / "favicon.ico")

    @server.get("/docs", include_in_schema=False)
    def redoc_html() -> FileResponse:
        """Render ReDoc HTML."""
        return FileResponse(static_dir / "redoc.html")

    # Setup sentry, if configured
    if conf.get_string("sentry.dsn") != "":
        sentry_sdk.init(
            dsn=conf.get_string("sentry.dsn"),
            environment=conf.get_string("sentry.environment"),
            traces_sample_rate=1,
            send_default_pii=True,
        )

        sentry_sdk.set_tag("server_name", "quartz_api")
        sentry_sdk.set_tag("version", importlib.metadata.version("quartz_api"))

    # Add routers to the server according to configuration
    if conf.get_string("api.routers") == "":
        log.warning("No routers configured. The API will not have any endpoints.")
    else:
        for r in conf.get_string("api.routers").split(","):
            try:
                mod = importlib.import_module(service.__name__ + f".{r}")
                server.include_router(mod.router)

                mod_description = getattr(mod, "__doc__", f"TODO: Add description for {r}")
                description = mod_description

            except ModuleNotFoundError as e:
                raise OSError(f"No such router router '{r}'") from e

    # Customize the OpenAPI schema
    server.openapi = lambda: _custom_openapi(server)

    # Store auth instance for middleware
    auth_instance = None

    # Override dependencies according to configuration
    match (conf.get_string("auth0.domain"), conf.get_string("auth0.audience")):
        case (_, "") | ("", _) | ("", ""):
            auth.auth_instance.instantiate_dummy()
            log.warning("disabled authentication. NOT recommended for production")

            description += """
            ### Authentication

            This API does not require authentication.
            """

        case (domain, audience):
            auth.auth_instance.instantiate_auth0(
                domain=domain,
                audience=audience,
            )
            auth_description = auth.make_api_auth_description(
                domain=domain,
                audience=audience,
                host_url=conf.get_string("api.host_url"),
                client_id=conf.get_string("auth0.client_id"),
            )

            description += auth_description

        case _:
            raise ValueError("Invalid Auth0 configuration")

    timezone: str = conf.get_string("api.timezone")
    server.dependency_overrides[models.get_timezone] = lambda: ZoneInfo(key=timezone)

    # Add middlewares
    server.add_middleware(
        CORSMiddleware,
        allow_origins=conf.get_string("api.origins").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if conf.get_string("backend.source") != "dataplatform":
        server.add_middleware(audit.RequestLoggerMiddleware)
    server.add_middleware(sentry.SentryUserMiddleware, auth_instance=auth_instance)

    # update description
    server.description = description

    if conf.get_string("apitally.client_id") != "":
        server.add_middleware(
            ApitallyMiddleware,
            client_id=conf.get_string("apitally.client_id"),
            env=conf.get_string("apitally.environment"),
            enable_request_logging=True,
            log_request_headers=True,
            log_request_body=True,
            log_response_body=True,
            capture_logs=True,
        )

    server.add_middleware(trace.TracerMiddleware)

    return server

conf = ConfigFactory.parse_file((pathlib.Path(__file__).parent / "server.conf").as_posix())
server = _create_server(conf)

def run() -> None:
    """Run the API using a gunicorn server."""
    cmd = [
        "gunicorn",
        "quartz_api.cmd.main:server",
        "--workers", str(conf.get_int("api.workers")),
        "--worker-class", "uvicorn.workers.UvicornWorker",
        "--bind", f"0.0.0.0:{conf.get_int('api.port')}",
    ]

    os.execvp("gunicorn", cmd) # noqa: S606 S607

if __name__ == "__main__":
    run()
