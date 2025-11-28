"""API providing access to OCF's Quartz Forecasts.

### Authentication

Some routes may require authentication. An access token can be obtained via CURL:

```
export AUTH=$(curl --request POST
   --url https://nowcasting-pro.eu.auth0.com/oauth/token
   --header 'content-type: application/json'
   --data '{
      "client_id":"TODO",
      "audience":"https://api.nowcasting.io/",
      "grant_type":"password",
      "username":"username",
      "password":"password"
    }'
)

export TOKEN=$(echo "${AUTH}" | jq '.access_token' | tr -d '"')
```

enabling authenticated requests using the Bearer scheme:

```
curl -X GET 'https://api.quartz.energy/<route>' -H "Authorization: Bearer $TOKEN"
```
"""

import logging
import pathlib
import sys
from importlib.metadata import version
from typing import Any

import uvicorn
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from pyhocon import ConfigFactory
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from quartz_api.internal.backends import DummyClient, QuartzClient
from quartz_api.internal.middleware import audit, auth
from quartz_api.internal.models import DatabaseInterface, get_db_client
from quartz_api.internal.service import regions, sites

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


def run() -> None:
    """Run the API using a uvicorn server."""
    # Get the application configuration from the environment
    conf = ConfigFactory.parse_file((pathlib.Path(__file__).parent / "server.conf").as_posix())

    # Create the FastAPI server
    server = FastAPI(
        version=version("quartz_api"),
        title="Quartz API",
        description=__doc__,
        openapi_tags=[{
            "name": "API Information",
            "description": "Routes providing information about the API.",
        }],
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
        import sentry_sdk
        sentry_sdk.init(
            dsn=conf.get_string("sentry.dsn"),
            environment=conf.get_string("sentry.environment"),
            traces_sample_rate=1,
        )

        sentry_sdk.set_tag("app_name", "quartz_api")
        sentry_sdk.set_tag("version", version("quartz_api"))

    # Override dependencies according to configuration
    match (conf.get_string("auth0.domain"), conf.get_string("auth0.audience")):
        case (_, "") | ("", _):
            server.dependency_overrides[auth.get_auth] = auth.DummyAuth()
            log.warning("disabled authentication. NOT recommended for production")
        case (domain, audience):
            server.dependency_overrides[auth.get_auth] = auth.Auth0(
                domain=domain,
                api_audience=audience,
                algorithm="RS256",
            )
        case _:
            raise ValueError("Invalid Auth0 configuration")

    db_instance: DatabaseInterface
    match conf.get_string("backend.source"):
        case "quartzdb":
            db_instance = QuartzClient(
                database_url=conf.get_string("backend.quartzdb.database_url"),
            )
        case "dummydb":
            db_instance = DummyClient()
            log.warning("disabled backend. NOT recommended for production")
        case _:
            raise ValueError(
                "Unknown backend. "
                f"Expected one of {list(conf.get('backend').keys())}",
            )

    server.dependency_overrides[get_db_client] = lambda: db_instance

    # Add middlewares
    server.add_middleware(
        CORSMiddleware,
        allow_origins=conf.get_string("api.origins").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    server.add_middleware(
        audit.RequestLoggerMiddleware,
        db_client=db_instance,
    )

    # Add routers to the server according to configuration
    for router_module in [sites, regions]:
        if conf.get_string("api.router") == router_module.__name__.split(".")[-1]:
            server.include_router(router_module.router)
            server.openapi_tags = [{
                "name": router_module.__name__.split(".")[-1].capitalize(),
                "description": router_module.__doc__,
            }, *server.openapi_tags]
            break

    # Customize the OpenAPI schema
    server.openapi = lambda: _custom_openapi(server)

    # Run the server with uvicorn
    uvicorn.run(
        server,
        host="0.0.0.0", # noqa: S104
        port=conf.get_int("api.port"),
        log_level=conf.get_string("api.loglevel"),
    )


if __name__ == "__main__":
    run()
