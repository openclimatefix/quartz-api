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
from typing import Any
from zoneinfo import ZoneInfo

import grpc
import sentry_sdk
from apitally.fastapi import ApitallyMiddleware
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from pydantic import BaseModel
from pyhocon import ConfigFactory, ConfigTree
from scalar_fastapi import get_scalar_api_reference
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from quartz_api.internal import models, service
from quartz_api.internal.backends import (
    DataPlatformStorage,
    DummyStorage,
    QuartzStorage,
)
from quartz_api.internal.middleware import audit, auth, ratelimit, sentry, trace
from quartz_api.internal.service.uk_national.endpoint_types import gsp_id_map
from quartz_api.internal.service.uk_national.gsp_router import _warm_forecast_all_cache

from ._logging import setup_json_logging

log = logging.getLogger(__name__)
logging.getLogger("hpack").setLevel(logging.WARNING)

static_dir = pathlib.Path(__file__).parent.parent / "static"


class GetHealthResponse(BaseModel):
    """Model for the health endpoint response."""

    status: int


def _custom_openapi(server: FastAPI, auth_config: dict[str, str] | None = None) -> dict[str, Any]:
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

    if auth_config:
        domain = auth_config["domain"]
        audience = auth_config["audience"]  # noqa: F841

        # Replace the auto-generated HTTPBearer scheme with a proper OAuth2
        # authorization code flow so Swagger UI shows the Auth0 redirect button.
        components = openapi_schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes.pop("HTTPBearer", None)
        security_schemes["oauth2"] = {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": f"https://{domain}/authorize",
                    "tokenUrl": f"https://{domain}/oauth/token",
                    "scopes": {
                        "openid": "OpenID",
                        "profile": "Profile",
                        "email": "Email",
                    },
                },
            },
        }

        # Update per-operation security requirements to reference oauth2 instead
        # of HTTPBearer so Swagger applies the token after the Auth0 redirect.
        for path_item in openapi_schema.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                operation["security"] = [
                    {"oauth2": []} if "HTTPBearer" in req else req
                    for req in operation.get("security", [])
                ]

    server.openapi_schema = openapi_schema

    return openapi_schema



def _create_v1_app(conf: ConfigTree, auth_openapi_config: dict[str, str] | None) -> FastAPI:
    """Create and configure the v1 FastAPI sub-application."""
    v1_mod = importlib.import_module(service.__name__ + ".v1")

    scalar_auth: dict = {}
    if auth_openapi_config:
        scalar_auth = {
            "preferredSecurityScheme": "oauth2",
            "oauth2": {
                "clientId": conf.get_string("auth0.client_id"),
                "scopes": "openid profile email",
            },
        }

    v1_app = FastAPI(
        title="Quartz API v1",
        version=importlib.metadata.version("quartz_api"),
        description=v1_mod.__doc__ or "",
        docs_url=None,
        redoc_url=None,
    )

    v1_app.state.limiter = ratelimit.limiter
    v1_app.include_router(v1_mod.router)
    v1_app.openapi = lambda: _custom_openapi(v1_app, auth_openapi_config)

    @v1_app.get("/docs", include_in_schema=False)
    async def v1_scalar_docs(request: Request) -> HTMLResponse:
        """Serve Scalar API reference for v1."""
        root_path = request.scope.get("root_path", "").rstrip("/")
        return get_scalar_api_reference(
            openapi_url=root_path + v1_app.openapi_url,
            title=v1_app.title,
            authentication=scalar_auth,
            persist_auth=True,
        )

    return v1_app


@asynccontextmanager
async def _lifespan(server: FastAPI, conf: ConfigTree) -> AsyncGenerator[None]:
    """Configure FastAPI app instance with startup and shutdown events."""
    storage: models.StorageInterface | None = None

    match conf.get_string("backend.source"):
        case "quartzdb":
            storage = QuartzStorage(
                database_url=conf.get_string("backend.quartzdb.database_url"),
            )
        case "dummydb":
            storage = DummyStorage()
            log.warning("disabled backend. NOT recommended for production")
        case "dataplatform":
            from ocf.dp.dp_data import service_pb2_grpc
            trace_interceptor = trace.TraceInterceptor()
            grpc_channel = grpc.aio.insecure_channel(
                target=conf.get_string("backend.dataplatform.host") \
                    + ":" + conf.get_string("backend.dataplatform.port"),
                    interceptors=[trace_interceptor],
            )
            client = service_pb2_grpc.DataPlatformDataServiceStub(grpc_channel)
            storage = DataPlatformStorage.from_dp(dp_client=client)

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
    warm_task: asyncio.Task | None = None

    if "uk_national" in conf.get_string("api.routers"):
        warm_task = asyncio.create_task(_warm_forecast_all_cache(server))

    warm_v1_task = None
    if "v1" in conf.get_string("api.routers").split(","):
        from quartz_api.internal.service.v1.cache import _warm_all_v1_caches
        v1_app = server.state.v1_app
        v1_app.dependency_overrides[models.get_storage_client] = lambda: storage
        warm_v1_task = asyncio.create_task(_warm_all_v1_caches(v1_app))

    yield

    if warm_task is not None:
        warm_task.cancel()
    if warm_v1_task is not None:
        warm_v1_task.cancel()

    gsp_id_map.clear()
    if grpc_channel:
        await grpc_channel.close()


def _create_server(conf: ConfigTree) -> FastAPI:
    """Configure FastAPI app instance with routes, dependencies, and middleware."""
    setup_json_logging(level=logging.getLevelName(conf.get_string("api.loglevel").upper()))
    description = "API providing access to OCF's Quartz Forecasts."
    server = FastAPI(
        debug=True,
        version=importlib.metadata.version("quartz_api"),
        lifespan=functools.partial(_lifespan, conf=conf),
        title="Quartz API",
        openapi_tags=[
            {
                "name": "API Information",
                "description": "Routes providing information about the API.",
            },
        ],
        docs_url=None,
        redoc_url=None,
    )

    FastAPICache.init(InMemoryBackend(), expire=120, prefix="fastapi-cache")

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

    @server.get("/scalar", include_in_schema=False)
    async def scalar_ui(request: Request) -> HTMLResponse:  # noqa: ARG001
        """Serve Scalar API reference."""
        return get_scalar_api_reference(
            openapi_url=server.openapi_url,
            title=server.title,
            persist_auth=True,
        )

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
            if r == "v1":
                continue  # handled as a sub-app below, after auth config is resolved
            try:
                mod = importlib.import_module(service.__name__ + f".{r}")
                server.include_router(mod.router)

                mod_description = getattr(mod, "__doc__", f"TODO: Add description for {r}")
                description = mod_description

            except ModuleNotFoundError as e:
                raise OSError(f"No such router router '{r}'") from e

    auth_openapi_config: dict[str, str] | None = None

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

            auth_openapi_config = {"domain": domain, "audience": audience}

        case _:
            raise ValueError("Invalid Auth0 configuration")

    # Customize the OpenAPI schema (after auth config is resolved)
    server.openapi = lambda: _custom_openapi(server, auth_openapi_config)

    # Mount v1 as a sub-app (after auth config is resolved so v1 gets OAuth2 config)
    if "v1" in conf.get_string("api.routers").split(","):
        v1_app = _create_v1_app(conf, auth_openapi_config)
        server.state.v1_app = v1_app
        server.mount("/v1", v1_app)

    timezone: str = conf.get_string("api.timezone")
    server.dependency_overrides[models.get_timezone] = lambda: ZoneInfo(key=timezone)

    # Add middlewares
    server.state.limiter = ratelimit.limiter
    server.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    server.add_middleware(SlowAPIMiddleware)
    server.add_middleware(
        CORSMiddleware,
        allow_origins=conf.get_string("api.origins").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if conf.get_string("backend.source") != "dataplatform":
        server.add_middleware(audit.RequestLoggerMiddleware)
    server.add_middleware(sentry.SentryUserMiddleware, auth_instance=None)
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

    # update description
    server.description = description

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
