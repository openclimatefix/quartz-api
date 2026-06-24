"""Unit tests for the regions router endpoints using mocks.

Tests cover:
- GET /sources
- GET /{source}/regions
- GET /{source}/{region}/generation
- GET /{source}/{region}/forecast
- GET /{source}/{region}/forecast/csv
- Verification of 404 error response when region is not found.
"""

import datetime as dt
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from quartz_api.internal import models

_REGIONAL_UUID = uuid4()
_REGIONAL_NAME = "ruvnl"


@pytest.mark.asyncio
async def test_get_sources(api_client: AsyncClient) -> None:
    """Test retrieving available sources."""
    resp = await api_client.get("/sources")
    assert resp.status_code == 200
    assert resp.json() == {"sources": ["solar"]}


@pytest.mark.asyncio
async def test_get_regions(api_client: AsyncClient, mock_storage: AsyncMock) -> None:
    """Test retrieving regions for solar."""
    mock_storage.get_locations.return_value = [
        models.Location(
            uuid=_REGIONAL_UUID,
            name=_REGIONAL_NAME,
            latitude=26.0,
            longitude=76.0,
            capacity_kilowatts=5_000,
            location_type=models.LocationType.REGION,
            energy_type=models.EnergyType.SOLAR,
        )
    ]
    resp = await api_client.get("/solar/regions")
    assert resp.status_code == 200
    assert resp.json() == {"regions": [_REGIONAL_NAME]}


@pytest.mark.asyncio
async def test_get_historic_generation_success(api_client: AsyncClient, mock_storage: AsyncMock) -> None:
    """Test retrieving historic generation for a valid region."""
    mock_storage.get_locations.return_value = [
        models.Location(
            uuid=_REGIONAL_UUID,
            name=_REGIONAL_NAME,
            latitude=26.0,
            longitude=76.0,
            capacity_kilowatts=5_000,
            location_type=models.LocationType.REGION,
            energy_type=models.EnergyType.SOLAR,
        )
    ]
    mock_storage.get_actual_generation.return_value = []

    resp = await api_client.get(f"/solar/{_REGIONAL_NAME}/generation")
    assert resp.status_code == 200
    assert resp.json() == {"values": []}

    # Verify site_api is passed as the observer name
    mock_storage.get_actual_generation.assert_called_once()
    kwargs = mock_storage.get_actual_generation.call_args.kwargs
    assert kwargs["observer_name"] == "site_api"


@pytest.mark.asyncio
async def test_get_historic_generation_404(api_client: AsyncClient, mock_storage: AsyncMock) -> None:
    """Test retrieving historic generation for an invalid region."""
    mock_storage.get_locations.return_value = []
    resp = await api_client.get("/solar/invalid_region/generation")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Region or GSP 'invalid_region' not found."


@pytest.mark.asyncio
async def test_get_forecast_success(api_client: AsyncClient, mock_storage: AsyncMock) -> None:
    """Test retrieving forecast for a valid region."""
    mock_storage.get_locations.return_value = [
        models.Location(
            uuid=_REGIONAL_UUID,
            name=_REGIONAL_NAME,
            latitude=26.0,
            longitude=76.0,
            capacity_kilowatts=5_000,
            location_type=models.LocationType.REGION,
            energy_type=models.EnergyType.SOLAR,
        )
    ]
    mock_storage.get_predicted_generation.return_value = []

    resp = await api_client.get(f"/solar/{_REGIONAL_NAME}/forecast?forecast_horizon=latest")
    assert resp.status_code == 200
    assert resp.json() == {"values": []}


@pytest.mark.asyncio
async def test_get_forecast_404(api_client: AsyncClient, mock_storage: AsyncMock) -> None:
    """Test retrieving forecast for an invalid region."""
    mock_storage.get_locations.return_value = []
    resp = await api_client.get("/solar/invalid_region/forecast?forecast_horizon=latest")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Region or GSP 'invalid_region' not found."


@pytest.mark.asyncio
async def test_get_forecast_csv_success(api_client: AsyncClient, mock_storage: AsyncMock) -> None:
    """Test retrieving forecast CSV for a valid region."""
    mock_storage.get_locations.return_value = [
        models.Location(
            uuid=_REGIONAL_UUID,
            name=_REGIONAL_NAME,
            latitude=26.0,
            longitude=76.0,
            capacity_kilowatts=5_000,
            location_type=models.LocationType.REGION,
            energy_type=models.EnergyType.SOLAR,
        )
    ]
    
    now = dt.datetime.now(tz=dt.UTC)
    mock_storage.get_predicted_generation.return_value = [
        models.PredictedGenerationValue(
            power_kilowatts=1200.0,
            valid_timestamp=now + dt.timedelta(hours=2),
            location_uuid=_REGIONAL_UUID,
            capacity_kilowatts=5_000,
            forecaster_name="blend",
            forecaster_version="1.0.0",
            created_timestamp=now,
            init_timestamp=now,
        )
    ]

    resp = await api_client.get(f"/solar/{_REGIONAL_NAME}/forecast/csv?forecast_horizon=latest")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
