"""Fixtures for setting up uk-national api with data-platform backend."""

import datetime
import os
import typing
from uuid import UUID

import pandas as pd
import pytest_asyncio
from dp_sdk.ocf import dp
from httpx import ASGITransport, AsyncClient
from pyhocon import ConfigFactory, ConfigTree

from quartz_api.cmd.main import _create_server
from quartz_api.internal import models
from quartz_api.internal.backends import DataPlatformStorage
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.tests.integration.conftest import forecast

auth_dep = typing.get_args(AuthDependency)[1].dependency


@pytest_asyncio.fixture(scope="session")
async def config_uk_national() -> None:
    """Returns the configuration tree for the UK National integration tests."""
    # set env variable to point to the config file
    os.environ["ROUTERS"] = "uk_national"
    os.environ["SOURCE"] = "dataplatform"

    yield ConfigFactory.parse_file(
        "src/quartz_api/cmd/server.conf",
    )
    # Clean up environment variable
    os.environ.pop("ROUTERS", None)
    os.environ.pop("SOURCE", None)


@pytest_asyncio.fixture(scope="module")
async def api_client_uk_national(
    config_uk_national: ConfigTree, dp_client: dp.DataPlatformDataServiceStub,
) -> AsyncClient:
    """Returns a TestClient for the FastAPI application."""
    app = _create_server(config_uk_national)

    db_instance = DataPlatformStorage.from_dp(dp_client=dp_client)
    db_instance.set_sync_client(os.environ["DATA_PLATFORM_HOST"], os.environ["DATA_PLATFORM_PORT"])
    app.dependency_overrides[models.get_storage_client] = lambda: db_instance

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(scope="module")
async def api_client_uk_national_admin(
    config_uk_national: ConfigTree, dp_client: dp.DataPlatformDataServiceStub,
) -> AsyncClient:
    """Test client with ocf:admin permissions."""
    app = _create_server(config_uk_national)
    db_instance = DataPlatformStorage.from_dp(dp_client=dp_client)
    app.dependency_overrides[models.get_storage_client] = lambda: db_instance
    app.dependency_overrides[auth_dep] = lambda: {"sub": "admin|123", "permissions": ["ocf:admin"]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(scope="module")
async def api_client_uk_national_non_admin(
    config_uk_national: ConfigTree, dp_client: dp.DataPlatformDataServiceStub,
) -> AsyncClient:
    """Test client without admin permissions."""
    app = _create_server(config_uk_national)
    db_instance = DataPlatformStorage.from_dp(dp_client=dp_client)
    app.dependency_overrides[models.get_storage_client] = lambda: db_instance
    app.dependency_overrides[auth_dep] = lambda: {"sub": "user|456", "permissions": []}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(scope="session")
async def make_national_forecast_values(
    dp_client: dp.DataPlatformDataServiceStub,
    national_location: dp.CreateLocationResponse,
) -> None:
    """Make forecast values for the national location."""
    # get time now, rounded down by 30 mins
    init_time_utc = pd.Timestamp.now(tz="UTC").floor("30min").to_pydatetime()
    for i in range(15):
        request = forecast(
            national_location.location_uuid,
            "blend_adjust",
            init_time_utc - datetime.timedelta(minutes=i * 30),
        )
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
