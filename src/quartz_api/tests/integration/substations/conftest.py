"""Fixtures for setting up substations api with data-platform backend."""

import datetime
import os
from uuid import UUID

import pandas as pd
import pytest_asyncio
from betterproto.lib.google.protobuf import Struct, Value
from dp_sdk.ocf import dp
from httpx import ASGITransport, AsyncClient
from pyhocon import ConfigFactory, ConfigTree

from quartz_api.cmd.main import _create_server
from quartz_api.internal import models
from quartz_api.internal.backends import DataPlatformStorage
from quartz_api.tests.integration.conftest import forecast


@pytest_asyncio.fixture(scope="session")
async def config_substations() -> None:
    """Returns the configuration tree for the substations integration tests."""
    os.environ["ROUTERS"] = "substations"
    os.environ["SOURCE"] = "dataplatform"

    yield ConfigFactory.parse_file(
        "src/quartz_api/cmd/server.conf",
    )
    os.environ.pop("ROUTERS", None)
    os.environ.pop("SOURCE", None)


@pytest_asyncio.fixture(scope="module")
async def api_client_substations(
    config_substations: ConfigTree, dp_client: dp.DataPlatformDataServiceStub,
) -> AsyncClient:
    """Returns a TestClient for the FastAPI application."""
    app = _create_server(config_substations)

    db_instance = DataPlatformStorage.from_dp(dp_client=dp_client)
    db_instance.set_sync_client(os.environ["DATA_PLATFORM_HOST"], os.environ["DATA_PLATFORM_PORT"])
    app.dependency_overrides[models.get_storage_client] = lambda: db_instance

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


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
