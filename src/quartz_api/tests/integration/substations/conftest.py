"""Fixtures for setting up substations api with data-platform backend."""

import datetime
import os
import time
from importlib.metadata import version
from uuid import UUID

import pandas as pd
import pytest_asyncio
from betterproto.lib.google.protobuf import Struct, Value
from dp_sdk.ocf import dp
from grpclib.client import Channel
from httpx import ASGITransport, AsyncClient
from pyhocon import ConfigFactory, ConfigTree
from testcontainers.core.container import DockerContainer
from testcontainers.postgres import PostgresContainer

from quartz_api.cmd.main import _create_server
from quartz_api.internal import models
from quartz_api.internal.backends import DataPlatformStorage


@pytest_asyncio.fixture(scope="session")
async def dp_client():
    """Fixture to spin up a PostgreSQL container for the entire test session.

    This fixture uses `testcontainers` to start a fresh PostgreSQL container and provides
    the connection URL dynamically for use in other fixtures.
    """
    with PostgresContainer(
        f"ghcr.io/openclimatefix/data-platform-pgdb:{version('dp_sdk')}",
        username="postgres",
        password="postgres",  # noqa S106
        dbname="postgres",
        env={"POSTGRES_HOST": "db"},
    ) as postgres:
        database_url = postgres.get_connection_url()
        database_url = database_url.replace("postgresql+psycopg2", "postgres")
        database_url = database_url.replace("localhost", "host.docker.internal")

        with DockerContainer(
            image=f"ghcr.io/openclimatefix/data-platform:{version('dp_sdk')}",
            env={"DATABASE_URL": database_url},
            ports=[50051],
        ) as data_platform_server:
            time.sleep(1)  # Give some time for the server to start

            port = data_platform_server.get_exposed_port(50051)
            host = data_platform_server.get_container_host_ip()
            os.environ["DATA_PLATFORM_HOST"] = host
            os.environ["DATA_PLATFORM_PORT"] = str(port)

            channel = Channel(host=host, port=port)
            client = dp.DataPlatformDataServiceStub(channel)
            yield client
            channel.close()


@pytest_asyncio.fixture(scope="session")
async def config():
    """Returns the configuration tree for the substations integration tests."""
    os.environ["ROUTERS"] = "substations"
    os.environ["SOURCE"] = "dataplatform"

    yield ConfigFactory.parse_file(
        "src/quartz_api/cmd/server.conf",
    )
    os.environ.pop("ROUTERS", None)
    os.environ.pop("SOURCE", None)


@pytest_asyncio.fixture(scope="session")
async def api_client(config: ConfigTree, dp_client: dp.DataPlatformDataServiceStub):
    """Returns a TestClient for the FastAPI application."""
    app = _create_server(config)

    db_instance = DataPlatformStorage.from_dp(dp_client=dp_client)
    app.dependency_overrides[models.get_storage_client] = lambda: db_instance

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def make_gsp_location(
    gsp_id: int,
    name: str,
) -> dp.CreateLocationRequest:
    """Create a GSP location request with a polygon geometry.

    GSP polygon is a simple square around the gsp_id coordinates.
    """
    # Create a polygon WKT that encloses the substation point
    # The polygon is a 1x1 degree box centered around (gsp_id, gsp_id)
    lat = float(gsp_id)
    lon = float(gsp_id)
    polygon_wkt = (
        f"POLYGON(({lon - 0.5} {lat - 0.5}, {lon + 0.5} {lat - 0.5}, "
        f"{lon + 0.5} {lat + 0.5}, {lon - 0.5} {lat + 0.5}, {lon - 0.5} {lat - 0.5}))"
    )

    metadata = Struct(fields={"gsp_id": Value(number_value=gsp_id)})

    return dp.CreateLocationRequest(
        location_name=name,
        energy_source=dp.EnergySource.SOLAR,
        geometry_wkt=polygon_wkt,
        location_type=dp.LocationType.GSP,
        effective_capacity_watts=10_000_000,  # 10 MW
        metadata=metadata,
        valid_from_utc=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
    )


def make_substation_location(
    name: str,
    gsp_id: int,
    lat: float,
    lon: float,
    capacity_watts: int = 1_000_000,  # 1 MW default
) -> dp.CreateLocationRequest:
    """Create a substation location request.

    The substation is a point within the GSP polygon.
    """
    metadata = Struct(fields={"gsp_id": Value(number_value=gsp_id)})

    return dp.CreateLocationRequest(
        location_name=name,
        energy_source=dp.EnergySource.SOLAR,
        geometry_wkt=f"POINT({lon} {lat})",
        location_type=dp.LocationType.PRIMARY_SUBSTATION,
        effective_capacity_watts=capacity_watts,
        metadata=metadata,
        valid_from_utc=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
    )


@pytest_asyncio.fixture(scope="session")
async def gsp_locations(dp_client: dp.DataPlatformDataServiceStub) -> list[UUID]:
    """Create GSP locations that will contain the substations.

    Creates 3 GSPs with ids 1, 2, 3.
    """
    location_uuids = []
    for gsp_id in range(1, 4):
        create_location_request = make_gsp_location(
            gsp_id=gsp_id,
            name=f"gsp_{gsp_id}",
        )
        res = await dp_client.create_location(create_location_request)
        location_uuids.append(res.location_uuid)

    return location_uuids


@pytest_asyncio.fixture(scope="session")
async def substation_locations(
    dp_client: dp.DataPlatformDataServiceStub,
    gsp_locations: list[UUID],  # noqa: ARG001 - ensures GSPs are created first
) -> list[tuple[UUID, str, int]]:
    """Create substation locations within GSP regions.

    Creates 5 substations across the 3 GSPs:
    - GSP 1: 2 substations
    - GSP 2: 2 substations
    - GSP 3: 1 substation
    """
    substations = [
        # (name, gsp_id, lat, lon, capacity_watts)
        ("substation_1a", 1, 1.0, 1.0, 1_000_000),  # Within GSP 1
        ("substation_1b", 1, 1.1, 1.1, 500_000),  # Within GSP 1
        ("substation_2a", 2, 2.0, 2.0, 2_000_000),  # Within GSP 2
        ("substation_2b", 2, 2.2, 2.2, 1_500_000),  # Within GSP 2
        ("substation_3a", 3, 3.0, 3.0, 3_000_000),  # Within GSP 3
    ]

    created_substations = []
    for name, gsp_id, lat, lon, capacity_watts in substations:
        create_location_request = make_substation_location(
            name=name,
            gsp_id=gsp_id,
            lat=lat,
            lon=lon,
            capacity_watts=capacity_watts,
        )
        res = await dp_client.create_location(create_location_request)
        created_substations.append((UUID(res.location_uuid), name, gsp_id))

    return created_substations


@pytest_asyncio.fixture(scope="session")
async def make_forecasters(dp_client: dp.DataPlatformDataServiceStub) -> None:
    """Create forecasters needed for the substations tests."""
    for model_name in ["blend", "blend_adjust"]:
        create_forecaster_request = dp.CreateForecasterRequest(
            name=model_name,
            version="0.0.0",
        )
        _ = await dp_client.create_forecaster(create_forecaster_request)


def forecast(
    location_uuid: str,
    name: str,
    init_time_utc: datetime.datetime,
) -> dp.CreateForecastRequest:
    """Create a forecast request for a location."""
    return dp.CreateForecastRequest(
        location_uuid=location_uuid,
        energy_source=dp.EnergySource.SOLAR,
        init_time_utc=init_time_utc,
        forecaster=dp.Forecaster(forecaster_name=name, forecaster_version="0.0.0"),
        values=[
            dp.CreateForecastRequestForecastValue(
                horizon_mins=i * 30,
                p50_fraction=i * 0.05,
                other_statistics_fractions={"p10": i * 0.06, "p90": i * 0.04},
            )
            for i in range(10)
        ],
        metadata=Struct(fields={"app_version": Value(string_value="1.0.0")}),
    )


@pytest_asyncio.fixture(scope="session")
async def make_gsp_forecast_values(
    dp_client: dp.DataPlatformDataServiceStub,
    gsp_locations: list[UUID],
    make_forecasters: None,  # noqa: ARG001 - ensures forecasters are created first
) -> None:
    """Create forecast values for the GSP locations.

    These forecasts will be used to derive substation forecasts.
    """
    init_time_utc = pd.Timestamp.now(tz="UTC").floor("30min").to_pydatetime()
    for location_uuid in gsp_locations:
        # Create forecasts for both blend and blend_adjust
        for model_name in ["blend", "blend_adjust"]:
            request = forecast(location_uuid, model_name, init_time_utc)
            _ = await dp_client.create_forecast(request)

        # Also create some historical forecasts
        for i in range(1, 5):
            request = forecast(
                location_uuid,
                "blend",
                init_time_utc - datetime.timedelta(minutes=i * 30),
            )
            _ = await dp_client.create_forecast(request)
