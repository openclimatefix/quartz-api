"""The main entrypoint to the application."""

import os
import uvicorn
import sentry_sdk


from quartz_api import internal
from quartz_api.internal.config import Config
from quartz_api.internal.service import get_db_client, server, version


cfg = Config()

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    environment=os.getenv("ENVIRONMENT", "local"),
    traces_sample_rate=1
)

sentry_sdk.set_tag("app_name", "quartz_api")
sentry_sdk.set_tag("version",version)

match cfg.SOURCE:
    case "quartzdb":
        if cfg.DB_URL == "" or cfg.DB_URL is None:
            raise OSError(f"DB_URL env var is required using db source: {cfg.SOURCE}")

        def get_db_client_override() -> internal.DatabaseInterface:
            return internal.inputs.quartzdb.Client(cfg.DB_URL)
    case "dummydb":

        def get_db_client_override() -> internal.DatabaseInterface:
            return internal.inputs.dummydb.Client()
    case _:
        raise ValueError(f"Unknown SOURCE: {cfg.SOURCE}. Expected 'dummydb'.")

# Dependency inject the desired database client
server.dependency_overrides[get_db_client] = get_db_client_override


def run() -> None:
    """Run the API using a uvicorn server."""
    uvicorn.run(
        "quartz_api.internal.service.server:server",
        host="0.0.0.0",
        port=cfg.PORT,
        reload=True,
        log_level="debug",
        workers=1,
    )


if __name__ == "__main__":
    run()
