"""Fixtures for setting up uk-national api with data-platform backend."""

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
async def dp_client() -> dp.DataPlatformDataServiceStub:
    """Fixture to spin up a PostgreSQL container for the entire test session.

    This fixture uses `testcontainers` to start a fresh PostgreSQL container and provides
    the connection URL dynamically for use in other fixtures.
    """
    # we use a specific postgres image with postgis and pgpartman installed
    # TODO make a release of this, not using logging tag.
    with PostgresContainer(
        "ghcr.io/openclimatefix/data-platform-pgdb:logging",
        username="postgres",
        password="postgres",  # noqa S106
        dbname="postgres",
        env={"POSTGRES_HOST": "db"},
    ) as postgres:
        database_url = postgres.get_connection_url()
        # we need to get ride of psycopg2, so the go driver works
        database_url = database_url.replace("postgresql+psycopg2", "postgres")
        # we need to change to host.docker.internal so the data platform container can see it
        # https://stackoverflow.com/questions/46973456/docker-access-localhost-port-from-container
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
async def config() -> None:
    """Returns the configuration tree for the UK National integration tests."""
    # set env variable to point to the config file
    os.environ["ROUTERS"] = "uk_national"
    os.environ["SOURCE"] = "dataplatform"

    yield ConfigFactory.parse_file(
        "src/quartz_api/cmd/server.conf",
    )
    # Clean up environment variable
    del os.environ["ROUTERS"]
    del os.environ["SOURCE"]


@pytest_asyncio.fixture(scope="session")
async def api_client(config: ConfigTree, dp_client: dp.DataPlatformDataServiceStub) -> AsyncClient:
    """Returns a TestClient for the FastAPI application."""
    app = _create_server(config)

    db_instance = DataPlatformStorage.from_dp(dp_client=dp_client)
    app.dependency_overrides[models.get_storage_client] = lambda: db_instance

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def make_location(
    name: str,
    metadata: dict,
    location_type: dp.LocationType = dp.LocationType.NATION,
) -> dp.CreateLocationRequest:
    """Create a location request."""
    create_location_request = dp.CreateLocationRequest(
        location_name=name,
        energy_source=dp.EnergySource.SOLAR,
        geometry_wkt="POINT(0 0)",
        location_type=location_type,
        effective_capacity_watts=10_000_000,
        metadata=metadata,
        valid_from_utc=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
    )

    return create_location_request


@pytest_asyncio.fixture(scope="session")
async def national_location(dp_client: dp.DataPlatformDataServiceStub) -> dp.CreateLocationResponse:
    """Make national location."""
    # add location gsp 0
    metadata = Struct(fields={"gsp_id": Value(number_value=0)})
    create_location_request = make_location(name="uk", metadata=metadata)
    create_location_response = await dp_client.create_location(create_location_request)

    return create_location_response


@pytest_asyncio.fixture(scope="session")
async def gsp_locations(dp_client: dp.DataPlatformDataServiceStub) -> list[UUID]:
    """Make national location."""
    # add location gsp 1 to 10
    location_uuids = []
    for i in range(1, 11):
        metadata = Struct(fields={"gsp_id": Value(number_value=i)})
        create_location_request = make_location(
            name=f"gsp_{i}",
            metadata=metadata,
            location_type=dp.LocationType.GSP,
        )
        res = await dp_client.create_location(create_location_request)
        location_uuids.append(res.location_uuid)

    return location_uuids


@pytest_asyncio.fixture(scope="session")
async def make_forecasters(dp_client: dp.DataPlatformDataServiceStub) -> None:
    """Make forecasters."""
    for model_name in ["blend", "blend_adjust"]:
        create_forecaster_request = dp.CreateForecasterRequest(
            name=model_name,
            version="0.0.0",
        )
        _ = await dp_client.create_forecaster(create_forecaster_request)


def forecast(location_uuid: str, name: str, init_time_utc: datetime) -> dp.CreateForecastRequest:
    """Create a forecast request."""
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
    )


@pytest_asyncio.fixture(scope="session")
async def make_national_forecast_values(
    dp_client: dp.DataPlatformDataServiceStub,
    national_location: dp.CreateLocationResponse,
) -> None:
    """Make forecast values for the national location."""
    # get time now, rounded down by 30 mins
    init_time_utc = pd.Timestamp.now(tz="UTC").floor("30min").to_pydatetime()
    request = forecast(national_location.location_uuid, "blend_adjust", init_time_utc)
    _ = await dp_client.create_forecast(request)


@pytest_asyncio.fixture(scope="session")
async def make_gsp_forecast_values(
    dp_client: dp.DataPlatformDataServiceStub,
    gsp_locations: list[UUID],
) -> None:
    """Make forecast values for the GSP locations."""
    # get time now, rounded down by 30 mins
    init_time_utc = pd.Timestamp.now(tz="UTC").floor("30min").to_pydatetime()
    for location_uuid in gsp_locations:
        request = forecast(location_uuid, "blend", init_time_utc)
        _ = await dp_client.create_forecast(request)


@pytest_asyncio.fixture(scope="session")
async def make_observers(dp_client: dp.DataPlatformDataServiceStub) -> None:
    """Make observers."""
    for model_name in ["pvlive_in_day", "pvlive_day_after"]:
        create_observer_request = dp.CreateObserverRequest(
            name=model_name,
        )
        _ = await dp_client.create_observer(create_observer_request)


def make_observation_values(
    location_uuid: str,
    observer_name: str,
    init_time_utc: datetime,
) -> dp.CreateObservationsRequest:
    """Make observation values for a given location and observer."""
    return dp.CreateObservationsRequest(
        location_uuid=location_uuid,
        energy_source=dp.EnergySource.SOLAR,
        observer_name=observer_name,
        values=[
            dp.CreateObservationsRequestValue(
                timestamp_utc=init_time_utc - datetime.timedelta(minutes=i * 30),
                value_watts=i,
            )
            for i in range(10)
        ],
    )


@pytest_asyncio.fixture(scope="session")
async def make_national_observation_values(
    dp_client: dp.DataPlatformDataServiceStub,
    national_location: dp.CreateLocationResponse,
) -> None:
    """Make observation values for the national location."""
    # get time now, rounded down by 30 mins
    init_time_utc = pd.Timestamp.now(tz="UTC").floor("30min").to_pydatetime()
    for model_name in ["pvlive_in_day", "pvlive_day_after"]:
        request = make_observation_values(
            location_uuid=national_location.location_uuid,
            observer_name=model_name,
            init_time_utc=init_time_utc,
        )
        _ = await dp_client.create_observations(request)


@pytest_asyncio.fixture(scope="session")
async def make_gsp_observation_values(
    dp_client: dp.DataPlatformDataServiceStub,
    gsp_locations: list[dp.CreateLocationResponse],
) -> None:
    """Make observation values for the GSP locations."""
    # get time now, rounded down by 30 mins
    init_time_utc = pd.Timestamp.now(tz="UTC").floor("30min").to_pydatetime()
    for model_name in ["pvlive_in_day", "pvlive_day_after"]:
        for location_uuid in gsp_locations:
            request = make_observation_values(
                location_uuid=location_uuid,
                observer_name=model_name,
                init_time_utc=init_time_utc,
            )
            _ = await dp_client.create_observations(request)
