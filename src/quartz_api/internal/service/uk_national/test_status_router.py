import datetime as dt
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import time_machine

from quartz_api.internal import models
from quartz_api.internal.service.uk_national.endpoint_types import gsp_id_map


@pytest.fixture(autouse=True)
def setup_national_map():
    """Populate the in-memory map with the National GSP (0) for validation."""
    gsp_id_map.clear()
    gsp_id_map[0] = models.Location(
        uuid=uuid4(),
        name="national",
        latitude=0.0,
        longitude=0.0,
        capacity_kilowatts=10000.0,
        metadata={"gsp_id": 0},
    )
    yield
    gsp_id_map.clear()


@pytest.mark.asyncio
async def test_check_last_forecast_run_valid(api_client, mock_storage: AsyncMock):
    """Verifies the route fetches the correct data and returns the timestamp."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    expected_created_time = frozen_time - dt.timedelta(hours=2)

    mock_storage.get_predicted_generation.return_value = [
        models.PredictedGenerationValue(
            power_kilowatts=1000.0,
            valid_timestamp=frozen_time,
            location_uuid=gsp_id_map[0].uuid,
            capacity_kilowatts=10000.0,
            forecaster_name="blend_adjust",
            forecaster_version="1.3.0",
            created_timestamp=expected_created_time,
            init_timestamp=expected_created_time,
        ),
    ]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get("/v0/solar/GB/check_last_forecast_run")

    assert response.status_code == 200

    assert response.json() == expected_created_time.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    mock_storage.get_predicted_generation.assert_called_once()
    kwargs = mock_storage.get_predicted_generation.call_args.kwargs
    assert kwargs["location_uuid"] == gsp_id_map[0].uuid
    assert kwargs["location_type"] == models.LocationType.NATION
    assert kwargs["energy_type"] == models.EnergyType.SOLAR
    assert kwargs["forecaster_name"] == "blend_adjust"

    assert kwargs["window_start"] == frozen_time - dt.timedelta(minutes=30)
    assert kwargs["window_end"] == frozen_time


@pytest.mark.asyncio
async def test_check_last_forecast_run_missing_location(api_client, mock_storage: AsyncMock):
    """Verifies a 404 is raised if the national location is not in the map."""
    gsp_id_map.clear()

    response = await api_client.get("/v0/solar/GB/check_last_forecast_run")

    assert response.status_code == 404
    assert response.json()["detail"] == "Location not found"

    mock_storage.get_predicted_generation.assert_not_called()

