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
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from pydantic import BaseModel
from pyhocon import ConfigFactory, ConfigTree
from scalar_fastapi import AgentScalarConfig, Theme, get_scalar_api_reference
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from quartz_api.constants import SUPPORT_EMAIL
from quartz_api.internal import models, service
from quartz_api.internal import s3 as s3_module
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


class ClearedInMemoryBackend(InMemoryBackend):
    """Custom in-memory cache backend that clears expired items."""

    async def periodic_clear_expired(self) -> None:
        """Periodically clear expired cache items."""
        while True:
            async with self._lock:
                now = self._now
                for_del = tuple(k for k, v in self._store.items() if v.ttl_ts < now)
                if len(for_del) > 0:
                    log.debug(
                        f"Clearing {len(for_del)} expired cache items, "
                        f"from {self._store.keys()} cached items",
                    )
                    for k in for_del:
                        del self._store[k]
            # Lets sleep for 10 minutes before checking again if there are any expired items
            await asyncio.sleep(600)


class GetHealthResponse(BaseModel):
    """Model for the health endpoint response."""

    status: int


def _custom_openapi(
    server: FastAPI,
    auth_config: dict[str, str] | None = None,
) -> dict[str, Any]:
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
            "email": SUPPORT_EMAIL,
        },
        routes=server.routes,
    )

    openapi_schema["info"]["x-logo"] = {"url": "/static/logo.png"}
    openapi_schema["tags"] = server.openapi_tags

    if auth_config:
        domain = auth_config["domain"]
        audience = auth_config["audience"]
        client_id = auth_config.get("client_id", "")

        # Replace the auto-generated HTTPBearer scheme with a proper OAuth2
        # authorization code flow so Swagger UI shows the Auth0 redirect button.
        components = openapi_schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes.pop("HTTPBearer", None)
        security_schemes["oauth2"] = {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    # Scalar reads x-scalar-secret-client-id and injects client_id
                    # into the authorization URL automatically.
                    "x-scalar-secret-client-id": client_id,
                    # audience is not a standard OAuth2 param; embed it in the URL
                    # so Auth0 receives it on the redirect.
                    "authorizationUrl": f"https://{domain}/authorize?audience={audience}",
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


# Scalar does not tick an optional parameter when a value is typed into it, so the row
# is left out of the request and the caller silently gets the default back. Upstream
# treats that as intended (scalar/scalar#7851); scalar/scalar#2558 asks for this.
# Scalar already does it for a row added by hand, so this only makes the rows that come
# from the OpenAPI document behave the same way. Clicks the box rather than setting
# `checked`, so Scalar's own handler runs and its request state follows. If Scalar
# renames these classes the listener stops firing and nothing else changes — the
# greying in `custom_css` still shows which rows are disabled.
# Scalar renders the Test Request panel in a fixed order that puts Cookies and Headers
# above Query Parameters, and offers no configuration for it (scalar/scalar exposes only
# tagsSorter / operationsSorter / orderSchemaPropertiesBy, none of which touch this
# panel). Query parameters are what callers actually set on this API, so lift them.
#
# The sections are flex children of the tabpanel, so `order` moves them. It cannot be
# pure CSS: the ids are sequential (`scalar-client-0-5`, `-6`, …) and shift by route
# depending on which sections exist, so neither id nor :nth-child identifies a section
# reliably — the only stable handle is the header text, which CSS cannot match.
#
# If Scalar renames these sections nothing gets an order and the panel falls back to
# Scalar's own order, which is today's behaviour.
_SCALAR_SECTION_ORDER_JS = r"""
<script>
  (function () {
    var ORDER = {
      'Authentication': 10,
      'Variables': 20,
      'Query Parameters': 30,
      'Request Body': 40,
      'Headers': 50,
      'Cookies': 60,
      'Code Snippet': 90,
    };
    function apply() {
      var panels = document.querySelectorAll(
        '.request-section-content[role="tabpanel"]',
      );
      for (var p = 0; p < panels.length; p++) {
        var kids = panels[p].children;
        for (var i = 0; i < kids.length; i++) {
          var el = kids[i];
          var head = el.querySelector('button, [role="button"], h2, h3');
          var label = head ? head.textContent.trim().replace(/\s+/g, ' ') : '';
          var order = null;
          for (var name in ORDER) {
            if (label.indexOf(name) === 0) { order = ORDER[name]; break; }
          }
          // A section we do not recognise sits mid-panel rather than jumping to an
          // end. The unlabelled one is a flex-grow spacer that pins Code Snippet to
          // the bottom, so it has to stay just above it.
          if (order === null) order = label ? 45 : 80;
          var want = String(order);
          if (el.style.order !== want) el.style.order = want;
        }
      }
    }
    // The panel is built when the modal opens and rebuilt when the route changes.
    var queued = false;
    new MutationObserver(function () {
      if (queued) return;
      queued = true;
      requestAnimationFrame(function () { queued = false; apply(); });
    }).observe(document.body, { childList: true, subtree: true });
    apply();
  })();
</script>
"""


_SCALAR_AUTO_ENABLE_JS = """
<script>
  (function () {
    document.addEventListener(
      'input',
      function (event) {
        var target = event.target;
        if (!target || !target.closest) return;
        var editor = target.closest('.code-input-lite__editor');
        if (!editor) return;
        var row = editor.closest('tr.group');
        if (!row) return;
        var box = row.querySelector('td:first-child input[type="checkbox"]');
        if (!box || box.checked) return;
        var cells = row.children;
        var filled = function (cell) {
          var field = cell && cell.querySelector('.code-input-lite');
          return !!field && !field.classList.contains('code-input-lite--empty');
        };
        // Both halves must be filled, so a nameless parameter is never enabled.
        if (!filled(cells[1]) || !filled(cells[cells.length - 1])) return;
        box.click();
      },
      true,
    );
  })();
</script>
"""



# The data platform answers with gRPC status codes; map the ones that describe the
# caller's request onto the matching HTTP status so a client sees a normal error.
_GRPC_TO_HTTP: dict[Any, int] = {
    grpc.StatusCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    grpc.StatusCode.INVALID_ARGUMENT: status.HTTP_400_BAD_REQUEST,
    grpc.StatusCode.PERMISSION_DENIED: status.HTTP_403_FORBIDDEN,
    grpc.StatusCode.UNAUTHENTICATED: status.HTTP_401_UNAUTHORIZED,
    grpc.StatusCode.ALREADY_EXISTS: status.HTTP_409_CONFLICT,
    grpc.StatusCode.RESOURCE_EXHAUSTED: status.HTTP_429_TOO_MANY_REQUESTS,
    grpc.StatusCode.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED: status.HTTP_504_GATEWAY_TIMEOUT,
}


async def _grpc_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer an uncaught data platform failure with an HTTP error, not a gRPC repr.

    Uncaught, an AioRpcError reaches the caller as its own repr, carrying
    debug_error_string and the peer address with it. The platform's `details()` is a
    short sentence and worth passing on when the caller can act on it; anything that
    maps to a 5xx is reported generically and left in full in the log.
    """
    code = exc.code() if isinstance(exc, grpc.aio.AioRpcError) else None
    http_status = _GRPC_TO_HTTP.get(code, status.HTTP_502_BAD_GATEWAY)
    log.error(f"Data platform call to {request.url.path} failed: {exc!r}")
    if http_status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        detail = "Upstream request failed. Please try again."
    else:
        detail = (exc.details() if isinstance(exc, grpc.aio.AioRpcError) else None) or (
            "The request was rejected upstream."
        )
    return JSONResponse(status_code=http_status, content={"detail": detail})

def _create_v1_app(
    conf: ConfigTree,
    auth_openapi_config: dict[str, str] | None,
    limiter: Limiter | None = None,
) -> FastAPI:
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
        title="Quartz API Documentation",
        version=importlib.metadata.version("quartz_api"),
        description=v1_mod.__doc__ or "",
        docs_url=None,
        redoc_url=None,
    )

    v1_app.state.limiter = limiter or ratelimit.limiter
    v1_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    v1_app.add_exception_handler(grpc.aio.AioRpcError, _grpc_exception_handler)
    v1_app.add_middleware(SlowAPIMiddleware)
    v1_app.include_router(v1_mod.router)
    v1_app.openapi = lambda: _custom_openapi(v1_app, auth_openapi_config)

    @v1_app.get("/docs", include_in_schema=False)
    async def v1_scalar_docs(request: Request) -> HTMLResponse:
        """Serve Scalar API reference for v1."""
        root_path = request.scope.get("root_path", "").rstrip("/")
        page = get_scalar_api_reference(
            openapi_url=root_path + v1_app.openapi_url,
            title=v1_app.title,
            authentication=scalar_auth,
            persist_auth=True,
            theme=Theme.ALTERNATE,
            dark_mode=True,
            scalar_favicon_url="/static/favicon.ico",
            default_open_all_tags=True,
            hide_dark_mode_toggle=True,
            agent=AgentScalarConfig(disabled=True),
            # Pinned: scalar_fastapi defaults to an unversioned jsdelivr URL, which
            # tracks latest and can change the docs UI — and the selectors the CSS
            # below relies on — with no deploy of ours.
            scalar_js_url=(
                "https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.68.0"
            ),
            order_schema_properties_by=("preserve"),
            hide_client_button=(True),
            custom_css="""
                      /* override theme colours */
                      :root .dark-mode {
                        --scalar-color-accent: #ffd053;
                      }
                      :root .light-mode {
                        --scalar-color-accent: #ffd053;
                      }
                      /* target the authorize button specifically */
                      .dark-mode .scalar-button:not(.scalar-button-ghost), .show-api-client-button {
                        background-color: var(--scalar-color-accent) !important;
                        color: #333 !important;
                        border-color: transparent !important;
                      }
                      .light-mode .scalar-button:not(.scalar-button-ghost),
                      .show-api-client-button {
                        background-color: var(--scalar-color-accent) !important;
                        color: #333 !important;
                        border-color: transparent !important;
                      }
                      /* hide "Open in API Client" Scalar link in Sidebar */
                      aside a.open-api-client-button {
                        display: none !important;
                      }
                      a.open-api-client-button + div {
                        padding-top: 0.75rem;
                      }
                      /* Scalar never ticks an optional parameter when you type a
                         value into it, and an unticked row is not sent — upstream
                         considers that intended (scalar/scalar#7851), and
                         scalar/scalar#2558 tracks changing it. So grey the disabled
                         rows to let the enabled ones read as the active set, and name
                         the state once a disabled row has a value in it.
                         `code-input-lite--empty` is Scalar's own empty-state class;
                         `:empty` would miss a field that was typed into and cleared,
                         which leaves a stray <br> behind. */
                      .scalar-data-table
                        tr.group:has(td:first-child input[type="checkbox"]:not(:checked))
                        .code-input-lite__editor {
                        color: var(--scalar-color-3);
                      }
                      /* A dropdown row has no `.code-input-lite`, so the rule above
                         misses it and its value read as active while unticked. The
                         value sits in a ghost button, greyed here to match. */
                      .scalar-data-table
                        tr.group:has(td:first-child input[type="checkbox"]:not(:checked))
                        td:last-child:not(:has(.code-input-lite))
                        button.scalar-button,
                      .scalar-data-table
                        tr.group:has(td:first-child input[type="checkbox"]:not(:checked))
                        td:last-child:not(:has(.code-input-lite))
                        button.scalar-button span {
                        color: var(--scalar-color-3) !important;
                      }
                      /* Scalar's own tick is near-white on dark and reads much like
                         the unticked one at a glance. The checkbox itself is
                         transparent and overlaid; the visible mark is the sibling
                         div, whose `color` the tick inherits. */
                      .scalar-data-table
                        td:first-child
                        input[type="checkbox"]:checked
                        + div {
                        color: var(--scalar-color-accent) !important;
                      }
                      .scalar-data-table
                        tr.group:has(td:first-child input[type="checkbox"]:not(:checked)):has(
                          td:last-child .code-input-lite:not(.code-input-lite--empty)
                        ) td:nth-child(2)::after {
                        content: "disabled";
                        margin-left: auto;
                        padding-right: 0.75rem;
                        align-self: center;
                        font-size: 11px;
                        color: var(--scalar-color-3);
                        white-space: nowrap;
                        pointer-events: none;
                      }
                    """,
        )
        html = page.body.decode()
        if "</body>" not in html:
            return page
        return HTMLResponse(
            html.replace(
                "</body>",
                _SCALAR_AUTO_ENABLE_JS + _SCALAR_SECTION_ORDER_JS + "</body>",
                1,
            ),
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
            for attempt in range(1, 6):
                try:
                    from ocf.dp.dp_data import service_pb2_grpc

                    trace_interceptor = trace.TraceInterceptor()
                    grpc_channel = grpc.aio.insecure_channel(
                        target=conf.get_string("backend.dataplatform.host")
                        + ":"
                        + conf.get_string("backend.dataplatform.port"),
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

                    break
                except Exception:
                    log.warning(
                        f"Failed to initialise dataplatform (attempt {attempt}/5)",
                    )
                    if attempt == 5:
                        raise
                    await asyncio.sleep(1 * (2 ** (attempt - 1)))

        case _ as backend_type:
            raise ValueError(f"Unknown backend: {backend_type}")

    server.dependency_overrides[models.get_storage_client] = lambda: storage
    warm_task: asyncio.Task | None = None

    if "uk_national" in conf.get_string("api.routers"):
        warm_task = asyncio.create_task(_warm_forecast_all_cache(server))

    warm_v1_task = None
    if "v1" in conf.get_string("api.routers").split(","):
        from quartz_api.internal.service.v1.cache import warm_all_v1_caches

        v1_app = server.state.v1_app
        v1_app.dependency_overrides[models.get_storage_client] = lambda: storage
        warm_v1_task = asyncio.create_task(warm_all_v1_caches(v1_app))

    # make sure cache is cleaned up every 10 seconds
    backend = FastAPICache.get_backend()
    if backend is not None and isinstance(backend, ClearedInMemoryBackend):
        clear_cache_periodically = asyncio.create_task(backend.periodic_clear_expired())

    yield

    if warm_task is not None:
        warm_task.cancel()
    if warm_v1_task is not None:
        warm_v1_task.cancel()

    if clear_cache_periodically is not None:
        clear_cache_periodically.cancel()

    gsp_id_map.clear()
    if grpc_channel:
        await grpc_channel.close()


def _create_server(conf: ConfigTree) -> FastAPI:
    """Configure FastAPI app instance with routes, dependencies, and middleware."""
    setup_json_logging(
        level=logging.getLevelName(conf.get_string("api.loglevel").upper()),
    )
    description = "API providing access to OCF's Quartz Forecasts."
    debug = conf.get_string("api.environment") != "production"
    server = FastAPI(
        debug=debug,
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
        swagger_ui_init_oauth={"usePkceWithAuthorizationCodeGrant": True},
        swagger_ui_parameters={"persistAuthorization": True},
    )

    FastAPICache.init(ClearedInMemoryBackend(), expire=120, prefix="fastapi-cache")

    # Configure satellite S3 access from the parsed config, before routers register.
    s3_module.configure(
        region=conf.get_string("satellite.aws_region"),
        geotiff_bucket=conf.get_string("satellite.geotiff_bucket"),
        icechunk_bucket=conf.get_string("satellite.icechunk_bucket"),
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
    if conf.get_string("api.routers") == "":
        log.warning("No routers configured. The API will not have any endpoints.")
    else:
        for r in conf.get_string("api.routers").split(","):
            if r == "v1":
                continue  # handled as a sub-app below, after auth config is resolved
            try:
                mod = importlib.import_module(service.__name__ + f".{r}")
                server.include_router(mod.router)

                mod_description = getattr(
                    mod,
                    "__doc__",
                    f"TODO: Add description for {r}",
                )
                description = mod_description

            except ModuleNotFoundError as e:
                raise OSError(f"No such router router '{r}'") from e

    auth_openapi_config: dict[str, str] | None = None

    # Override dependencies according to configuration
    match (conf.get_string("auth0.domain"), conf.get_string("auth0.audience")):
        case (_, "") | ("", _) | ("", ""):
            # Never fall back to unauthenticated access in production
            if conf.get_string("api.environment") == "production":
                raise ValueError(
                    "Auth0 is not configured: AUTH0_DOMAIN and AUTH0_AUDIENCE are required "
                    "in production",
                )
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

            auth_openapi_config = {
                "domain": domain,
                "audience": audience,
                "client_id": conf.get_string("auth0.client_id"),
            }
            server.swagger_ui_init_oauth = {
                "usePkceWithAuthorizationCodeGrant": True,
                "clientId": conf.get_string("auth0.client_id"),
                "scopes": "openid profile email offline_access",
                "additionalQueryStringParams": {"audience": audience},
            }

        case _:
            raise ValueError("Invalid Auth0 configuration")

    # Customize the OpenAPI schema (after auth config is resolved)
    server.openapi = lambda: _custom_openapi(server)

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
    server.add_exception_handler(grpc.aio.AioRpcError, _grpc_exception_handler)
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
    server.add_middleware(sentry.SentryUserMiddleware, auth_instance=auth.auth_instance)
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
    server.add_middleware(GZipMiddleware, minimum_size=1000)

    return server


conf = ConfigFactory.parse_file(
    (pathlib.Path(__file__).parent / "server.conf").as_posix(),
)
server = _create_server(conf)


def run() -> None:
    """Run the API using a gunicorn server."""
    cmd = [
        "gunicorn",
        "quartz_api.cmd.main:server",
        "--workers",
        str(conf.get_int("api.workers")),
        "--worker-class",
        "uvicorn.workers.UvicornWorker",
        "--bind",
        f"0.0.0.0:{conf.get_int('api.port')}",
    ]

    os.execvp("gunicorn", cmd)  # noqa: S606 S607


if __name__ == "__main__":
    run()
