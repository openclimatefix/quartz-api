"""Fixtures for setting up uk-national api with data-platform backend."""

import datetime
import os
import time
from importlib.metadata import version
from uuid import UUID

import pytest_asyncio
from betterproto.lib.google.protobuf import Struct, Value
from dp_sdk.ocf import dp
from grpclib.client import Channel
from testcontainers.core.container import DockerContainer
from testcontainers.postgres import PostgresContainer


@pytest_asyncio.fixture(scope="session")
async def dp_client() -> dp.DataPlatformDataServiceStub:
    """Fixture to spin up a PostgreSQL container for the entire test session.

    This fixture uses `testcontainers` to start a fresh PostgreSQL container and provides
    the connection URL dynamically for use in other fixtures.
    """
    # we use a specific postgres image with postgis and pgpartman installed
    # TODO make a release of this, not using logging tag.
    with PostgresContainer(
        f"ghcr.io/openclimatefix/data-platform-pgdb:{version('dp_sdk')}",
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
        metadata=Struct(fields={"app_version": Value(string_value="1.2.3")}),
    )


def make_location(
    name: str,
    gsp_id: int,
    metadata: dict,
    location_type: dp.LocationType = dp.LocationType.NATION,
) -> dp.CreateLocationRequest:
    """Create a location request."""
    lat = float(gsp_id)
    lon = float(gsp_id)
    polygon_wkt = (
        f"POLYGON(({lon - 0.5} {lat - 0.5}, {lon + 0.5} {lat - 0.5}, "
        f"{lon + 0.5} {lat + 0.5}, {lon - 0.5} {lat + 0.5}, {lon - 0.5} {lat - 0.5}))"
    )
    create_location_request = dp.CreateLocationRequest(
        location_name=name,
        energy_source=dp.EnergySource.SOLAR,
        geometry_wkt=polygon_wkt,
        location_type=location_type,
        effective_capacity_watts=10_000_000,
        metadata=metadata,
        valid_from_utc=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
    )

    return create_location_request


@pytest_asyncio.fixture(scope="session")
async def gsp_locations(dp_client: dp.DataPlatformDataServiceStub) -> list[UUID]:
    """Make national location."""
    # add location gsp 1 to 10
    location_uuids = []
    for i in range(1, 11):
        metadata = Struct(fields={"gsp_id": Value(number_value=i)})
        create_location_request = make_location(
            name=f"gsp_{i}",
            gsp_id=i,
            metadata=metadata,
            location_type=dp.LocationType.GSP,
        )
        res = await dp_client.create_location(create_location_request)
        location_uuids.append(res.location_uuid)

    return location_uuids


@pytest_asyncio.fixture(scope="session")
async def national_location(dp_client: dp.DataPlatformDataServiceStub) -> dp.CreateLocationResponse:
    """Make national location."""
    # add location gsp 0
    metadata = Struct(fields={"gsp_id": Value(number_value=0)})
    create_location_request = make_location(name="uk", gsp_id=0, metadata=metadata)
    create_location_response = await dp_client.create_location(create_location_request)

    return create_location_response
