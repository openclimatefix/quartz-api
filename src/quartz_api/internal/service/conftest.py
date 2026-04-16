"""Pytest fixtures for unit testing the Quartz API."""

import os
from unittest.mock import AsyncMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pyhocon import ConfigFactory, ConfigTree

from quartz_api.cmd.main import _create_server
from quartz_api.internal import models


@pytest_asyncio.fixture(scope="session")
async def config_all_routers() -> None:
    """Returns the configuration tree for all routers."""
    os.environ["ROUTERS"] = "uk_national,substations,sites,regions"
    os.environ["SOURCE"] = "dataplatform"
    yield ConfigFactory.parse_file("src/quartz_api/cmd/server.conf")
    os.environ.pop("ROUTERS", None)
    os.environ.pop("SOURCE", None)


@pytest_asyncio.fixture(scope="function")
async def mock_storage() -> AsyncMock:
    """Creates a fresh mocked StorageInterface for every unit test."""
    mock = AsyncMock(spec=models.StorageInterface)
    mock.get_predicted_generation.return_value = []
    mock.get_predicted_generation_snapshot.return_value = []
    mock.get_actual_generation.return_value = []
    mock.get_actual_generation_snapshot.return_value = []
    yield mock


@pytest_asyncio.fixture(scope="function")
async def api_client(
    config_all_routers: ConfigTree,
    mock_storage: AsyncMock,
) -> AsyncClient:
    """API client with mocked Storage Interface."""
    app = _create_server(config_all_routers)
    app.dependency_overrides[models.get_storage_client] = lambda: mock_storage
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
