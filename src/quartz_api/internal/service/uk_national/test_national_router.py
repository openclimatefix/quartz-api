import datetime as dt
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import time_machine

from quartz_api.internal import models

from .endpoint_types import gsp_id_map


@pytest.fixture(autouse=True)
def setup_national_gsp_map():
    """Populate the in-memory map so the router can find the national UUID."""
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
async def test_national_forecast_default(api_client, mock_storage: AsyncMock):
    """Verifies the default /forecast route returns formatted data."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

    mock_storage.get_locations.return_value = [gsp_id_map[0]]
    mock_storage.get_predicted_generation.return_value = [
        models.PredictedGenerationValue(
            power_kilowatts=4200.0,
            valid_timestamp=frozen_time + dt.timedelta(hours=1),
            location_uuid=gsp_id_map[0].uuid,
            capacity_kilowatts=10000.0,
            forecaster_name="blend_adjust",
            forecaster_version="1.3.0",
            created_timestamp=frozen_time,
            init_timestamp=frozen_time,
            plevels_kilowatts={
                "p90": 4600.0,
                "p2": 3500.0,
                "p98": 4900.0,
                "p25": 4000.0,
                "p75": 4400.0,
                "p10": 3800.0,
            },
            metadata={},
        ),
    ]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get("/v0/solar/GB/national/forecast")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1

    # Check kW -> MW formatting
    assert data[0]["expectedPowerGenerationMegawatts"] == 4.2

    # check the p-level sorting
    assert list(data[0]["plevels"].keys()) == [
        "plevel_2", "plevel_10", "plevel_25", "plevel_75", "plevel_90", "plevel_98",
    ]
    assert data[0]["plevels"] == {
        "plevel_2": 3.5,
        "plevel_10": 3.8,
        "plevel_25": 4.0,
        "plevel_75": 4.4,
        "plevel_90": 4.6,
        "plevel_98": 4.9,
    }

    # Verify the database was called with the correct defaults
    mock_storage.get_predicted_generation.assert_called()
    kwargs = mock_storage.get_predicted_generation.call_args.kwargs
    assert kwargs["forecast_horizon_minutes"] == 0
    assert kwargs["forecaster_name"] == "blend_adjust"


@pytest.mark.asyncio
async def test_national_forecast_horizon(api_client, mock_storage: AsyncMock):
    """Verifies horizon minutes are passed correctly to the DB."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    mock_storage.get_locations.return_value = [gsp_id_map[0]]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get(
            "/v0/solar/GB/national/forecast?forecast_horizon_minutes=120",
        )

    assert response.status_code == 200

    # Check that the router correctly read the query param and handed it to the DB mock
    kwargs = mock_storage.get_predicted_generation.call_args.kwargs
    assert kwargs["forecast_horizon_minutes"] == 120


@pytest.mark.asyncio
async def test_national_forecast_metadata(api_client, mock_storage: AsyncMock):
    """Verifies include_metadata=true returns the nested dict structure."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    mock_storage.get_locations.return_value = [gsp_id_map[0]]

    mock_storage.get_predicted_generation.return_value = [
        models.PredictedGenerationValue(
            power_kilowatts=4200.0,
            valid_timestamp=frozen_time,
            location_uuid=gsp_id_map[0].uuid,
            capacity_kilowatts=10000.0,
            forecaster_name="blend_adjust",
            forecaster_version="1.3.0",
            created_timestamp=frozen_time,
            init_timestamp=frozen_time,
            plevels_kilowatts={},
            metadata={"app_version": "v1.9.9", "nwp_run_time": "2026-01-01T10:00"},
        ),
    ]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get(
            "/v0/solar/GB/national/forecast?include_metadata=true",
        )

    assert response.status_code == 200
    data = response.json()

    # Assert it returns the nested object, not a List
    assert isinstance(data, dict)
    assert data["model"]["name"] == "blend"  # Check the model name has been returned to blend
    assert data["model"]["version"] == "v1.9.9"
    assert len(data["forecastValues"]) == 1


@pytest.mark.asyncio
async def test_national_pvlive_default(api_client, mock_storage: AsyncMock):
    """Verifies pvlive returns descending sorted values."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    mock_storage.get_locations.return_value = [gsp_id_map[0]]

    # Return out-of-order data to ensure the router sorts it descending
    mock_storage.get_actual_generation.return_value = [
        models.ActualGenerationValue(
            power_kilowatts=1000.0,
            valid_timestamp=frozen_time - dt.timedelta(hours=2),
            location_uuid=gsp_id_map[0].uuid,
            capacity_kilowatts=10000.0,
            observer_name="pvlive_in_day",
        ),
        models.ActualGenerationValue(
            power_kilowatts=2000.0,
            valid_timestamp=frozen_time - dt.timedelta(hours=1),
            location_uuid=gsp_id_map[0].uuid,
            capacity_kilowatts=10000.0,
            observer_name="pvlive_in_day",
        ),
    ]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get("/v0/solar/GB/national/pvlive")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    # Ensure they were sorted by latest time first
    assert data[0]["solarGenerationKw"] == 2000.0
    assert data[1]["solarGenerationKw"] == 1000.0

    # Ensure DB was called with default "in-day" parameter
    kwargs = mock_storage.get_actual_generation.call_args.kwargs
    assert kwargs["observer_name"] == "pvlive_in_day"


@pytest.mark.asyncio
async def test_national_pvlive_day_after(api_client, mock_storage: AsyncMock):
    """Verifies regime parameter is coerced correctly."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    mock_storage.get_locations.return_value = [gsp_id_map[0]]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get(
            "/v0/solar/GB/national/pvlive?regime=day-after",
        )

    assert response.status_code == 200

    # Verify the router converted "day-after" to "pvlive_day_after"
    kwargs = mock_storage.get_actual_generation.call_args.kwargs
    assert kwargs["observer_name"] == "pvlive_day_after"

@pytest.mark.asyncio
async def test_national_last_updated_default(api_client, mock_storage: AsyncMock):
    """Verifies last_updated route returns the correct timestamp and uses correct defaults."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    expected_created_time = frozen_time - dt.timedelta(hours=1)

    # Mock the database response with a specific created_timestamp
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
        response = await api_client.get("/v0/solar/GB/national/forecast/last_updated")

    assert response.status_code == 200

    # FastAPI automatically serializes dt.datetime to an ISO 8601 string
    data = response.json()
    assert data == expected_created_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Verify the database was called with the exactly calculated time windows
    mock_storage.get_predicted_generation.assert_called_once()
    kwargs = mock_storage.get_predicted_generation.call_args.kwargs
    assert kwargs["location_uuid"] == gsp_id_map[0].uuid
    assert kwargs["location_type"] == models.LocationType.NATION
    assert kwargs["forecaster_name"] == "blend_adjust"

    # Assert window math (+/- 30 mins from "now")
    assert kwargs["window_start"] == frozen_time - dt.timedelta(minutes=30)
    assert kwargs["window_end"] == frozen_time + dt.timedelta(minutes=30)

