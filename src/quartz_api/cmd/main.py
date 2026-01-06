"""API providing access to OCF's Quartz Forecasts.

### Authentication

Some routes may require authentication. An access token can be obtained via cURL:

```
export AUTH=$(curl --request POST
   --url https://nowcasting-dev.eu.auth0.com/oauth/token
   --header 'content-type: application/json'
   --data '{
      "client_id":"ONgyGHNXnOW8NjrlkuxLoEdOa5FkELEn",
      "audience":"https://nowcasting-api-eu-auth0.com/",
      "grant_type":"password",
      "username":"username",
      "password":"password"
    }'
)

export TOKEN=$(echo "${AUTH}" | jq '.access_token' | tr -d '"')
```

enabling authenticated requests using the Bearer scheme:

```
curl -X GET 'http://uk-development-quartz-api.eu-west-1.elasticbeanstalk.com/<route>' -H "Authorization: Bearer $TOKEN"
```
"""  # noqa: E501

import functools
import importlib
import importlib.metadata
import logging
import pathlib
import sys
from collections.abc import Generator
from contextlib import asynccontextmanager
from typing import Any

import sentry_sdk
import uvicorn
from apitally.fastapi import ApitallyMiddleware
from dp_sdk.ocf import dp
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from grpclib.client import Channel
from pydantic import BaseModel
from pyhocon import ConfigFactory, ConfigTree
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from quartz_api.internal import models, service
from quartz_api.internal.backends import DataPlatformClient, DummyClient, QuartzClient
from quartz_api.internal.middleware import audit, auth, sentry, time

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
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
async def _lifespan(server: FastAPI, conf: ConfigTree) -> Generator[None]:
    """Configure FastAPI app instance with startup and shutdown events."""
    db_instance: models.DatabaseInterface | None = None
    grpc_channel: Channel | None = None

    match conf.get_string("backend.source"):
        case "quartzdb":
            db_instance = QuartzClient(
                database_url=conf.get_string("backend.quartzdb.database_url"),
            )
        case "dummydb":
            db_instance = DummyClient()
            log.warning("disabled backend. NOT recommended for production")
        case "dataplatform":
            grpc_channel = Channel(
                host=conf.get_string("backend.dataplatform.host"),
                port=conf.get_int("backend.dataplatform.port"),
            )
            client = dp.DataPlatformDataServiceStub(channel=grpc_channel)
            db_instance = DataPlatformClient.from_dp(dp_client=client)
        case _ as backend_type:
            raise ValueError(f"Unknown backend: {backend_type}")

    server.dependency_overrides[models.get_db_client] = lambda: db_instance

    yield

    if grpc_channel:
        grpc_channel.close()


def _create_server(conf: ConfigTree) -> FastAPI:
    """Configure FastAPI app instance with routes, dependencies, and middleware."""
    server = FastAPI(
        version=importlib.metadata.version("quartz_api"),
        lifespan=functools.partial(_lifespan, conf=conf),
        title="Quartz API",
        description=__doc__,
        openapi_tags=[
            {
                "name": "API Information",
                "description": "Routes providing information about the API.",
            },
        ],
        docs_url="/swagger",
        redoc_url=None,
    )

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
    for r in conf.get_string("api.routers").split(","):
        try:
            mod = importlib.import_module(service.__name__ + f".{r}")
            server.include_router(mod.router)
        except ModuleNotFoundError as e:
            raise OSError(f"No such router router '{r}'") from e
        server.openapi_tags = [
            {
                "name": mod.__name__.split(".")[-1].capitalize(),
                "description": mod.__doc__,
            },
            *server.openapi_tags,
        ]

    # Customize the OpenAPI schema
    server.openapi = lambda: _custom_openapi(server)

    # Store auth instance for middleware
    auth_instance = None

    # Override dependencies according to configuration
    match (conf.get_string("auth0.domain"), conf.get_string("auth0.audience")):
        case (_, "") | ("", _):
            auth_instance = auth.DummyAuth()
            server.dependency_overrides[auth.get_auth] = auth_instance
            log.warning("disabled authentication. NOT recommended for production")
        case (domain, audience):
            auth_instance = auth.Auth0(
                domain=domain,
                api_audience=audience,
                algorithm="RS256",
            )
            server.dependency_overrides[auth.get_auth] = auth_instance
        case _:
            raise ValueError("Invalid Auth0 configuration")

    timezone: str = conf.get_string("api.timezone")
    server.dependency_overrides[models.get_timezone] = lambda: timezone

    # Add middlewares
    server.add_middleware(
        CORSMiddleware,
        allow_origins=conf.get_string("api.origins").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    server.add_middleware(audit.RequestLoggerMiddleware)
    server.add_middleware(time.TimerMiddleware)
    server.add_middleware(sentry.SentryUserMiddleware, auth_instance=auth_instance)

    if conf.get_string("apitally.client_id") != "":
        server.add_middleware(
            ApitallyMiddleware,
            client_id=conf.get_string("apitally.client_id"),
            environment=conf.get_string("apitally.environment"),
            enable_request_logging=True,
            log_request_headers=True,
            log_request_body=True,
            log_response_body=True,
            capture_logs=True,
        )

    return server


def run() -> None:
    """Run the API using a uvicorn server."""
    # Get the application configuration from the environment
    conf = ConfigFactory.parse_file((pathlib.Path(__file__).parent / "server.conf").as_posix())

    server = _create_server(conf=conf)

    # Run the server with uvicorn
    uvicorn.run(
        server,
        host="0.0.0.0",  # noqa: S104
        port=conf.get_int("api.port"),
        log_level=conf.get_string("api.loglevel"),
    )


if __name__ == "__main__":
    run()

