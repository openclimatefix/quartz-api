"""Fixtures for setting up uk-national api with data-platform backend."""

import datetime
from uuid import UUID

import pandas as pd
import pytest_asyncio
from ocf.dp.dp_data import service_pb2_grpc

from quartz_api.tests.integration.conftest import forecast


@pytest_asyncio.fixture(scope="session")
async def make_national_forecast_values(
    dp_client: service_pb2_grpc.DataPlatformDataServiceStub,
    gsp_locations: list[UUID],
) -> None:
    """Make forecast values for the national location."""
    # get time now, rounded down by 30 mins
    init_time_utc = pd.Timestamp.now(tz="UTC").floor("30min").to_pydatetime()
    for i in range(15):
        request = forecast(
            str(gsp_locations[0]),
            "blend_adjust",
            init_time_utc - datetime.timedelta(minutes=i * 30),
        )
        _ = await dp_client.CreateForecast(request)


@pytest_asyncio.fixture(scope="session")
async def make_gsp_forecast_values(
    dp_client: service_pb2_grpc.DataPlatformDataServiceStub,
    gsp_locations: list[UUID],
) -> None:
    """Make forecast values for the GSP locations."""
    # get time now, rounded down by 30 mins
    init_time_utc = pd.Timestamp.now(tz="UTC").floor("30min").to_pydatetime()
    for location_uuid in gsp_locations[1:]:
        request = forecast(str(location_uuid), "blend", init_time_utc)
        _ = await dp_client.CreateForecast(request)
