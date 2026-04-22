import datetime as dt
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import time_machine

from quartz_api.internal import models

from .endpoint_types import gsp_id_map


@pytest.fixture(autouse=True)
def setup_gsp_map():
    """Populate the in-memory map with 3 fake GSPs + national so the router can resolve IDs."""
    gsp_id_map.clear()
    for i in range(0, 4):
        gsp_id_map[i] = models.Location(
            uuid=uuid4(),
            name=f"gsp_{i}",
            latitude=50.0,
            longitude=0.0,
            capacity_kilowatts=10000.0,
            metadata={"gsp_id": i},
        )
    yield
    gsp_id_map.clear()


@pytest.mark.asyncio
async def test_gsp_forecast_specific_valid(
    api_client,
    mock_storage: AsyncMock,
):
    """Verifies the single GSP forecast route formats data correctly."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    gsp_1_uuid = gsp_id_map[1].uuid

    mock_storage.get_predicted_generation.return_value = [
        models.PredictedGenerationValue(
            power_kilowatts=2500.0,
            valid_timestamp=frozen_time + dt.timedelta(hours=1),
            location_uuid=gsp_1_uuid,
            capacity_kilowatts=10000.0,
            forecaster_name="blend",
            forecaster_version="1.3.0",
            created_timestamp=frozen_time,
            init_timestamp=frozen_time,
            metadata={},
        ),
    ]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get(
            "/v0/solar/GB/gsp/1/forecast?forecast_horizon_minutes=60",
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    # Assert MW conversion and normalization
    assert data[0]["expectedPowerGenerationMegawatts"] == 2.5
    assert data[0]["expectedPowerGenerationNormalized"] == 0.25  # 2500 / 10000

    # Assert DB was called with correct parameters
    mock_storage.get_predicted_generation.assert_called_once()
    kwargs = mock_storage.get_predicted_generation.call_args.kwargs
    assert kwargs["location_uuid"] == gsp_1_uuid
    assert kwargs["forecast_horizon_minutes"] == 60


@pytest.mark.asyncio
async def test_gsp_forecast_specific_invalid(api_client):
    """Verifies invalid GSP ID returns an empty list."""
    response = await api_client.get("/v0/solar/GB/gsp/999/forecast")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_gsp_pvlive_specific(api_client, mock_storage: AsyncMock):
    """Verifies regime formatting and descending sort."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

    # Return out-of-order data
    mock_storage.get_actual_generation.return_value = [
        models.ActualGenerationValue(
            power_kilowatts=1000.0,
            valid_timestamp=frozen_time - dt.timedelta(hours=2),
            location_uuid=gsp_id_map[1].uuid,
            capacity_kilowatts=10000.0,
            observer_name="pvlive_day_after",
        ),
        models.ActualGenerationValue(
            power_kilowatts=2000.0,
            valid_timestamp=frozen_time - dt.timedelta(hours=1),
            location_uuid=gsp_id_map[1].uuid,
            capacity_kilowatts=10000.0,
            observer_name="pvlive_day_after",
        ),
    ]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get(
            "/v0/solar/GB/gsp/1/pvlive?regime=day-after",
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["solarGenerationKw"] == 2000.0  # Sorts descending

    # Check regime string was corrected from "day-after" to "pvlive_day_after"
    kwargs = mock_storage.get_actual_generation.call_args.kwargs
    assert kwargs["observer_name"] == "pvlive_day_after"


@pytest.mark.asyncio
async def test_gsp_forecast_all_filtered_compact(
    api_client,
    mock_storage: AsyncMock,
):
    """Verifies compact=true JSON structuring when filtering by gsp_ids."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    target_ts = frozen_time + dt.timedelta(hours=1)

    mock_storage.get_predicted_generation.return_value = [
        models.PredictedGenerationValue(
            power_kilowatts=1500.0,
            valid_timestamp=target_ts,
            location_uuid=gsp_id_map[1].uuid,
            capacity_kilowatts=10000.0,
            forecaster_name="blend",
            forecaster_version="1.3.0",
        ),
    ]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get(
            "/v0/solar/GB/gsp/forecast/all/?gsp_ids=1,2&compact=true",
        )

    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) == 1
    # Check the compact structure: { datetime_utc: ..., forecast_values: {"1": 1.5} }
    assert "datetimeUtc" in data[0]
    assert data[0]["forecastValues"]["1"] == 1.5

    # Ensure it used the granular db.get_predicted_generation route because gsp_ids was set
    mock_storage.get_predicted_generation.assert_called()
    mock_storage.get_predicted_generation_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_gsp_forecast_all_default(api_client, mock_storage: AsyncMock):
    """Verifies default route hits the snapshot DB method."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

    # Fake snapshot response
    mock_storage.get_predicted_generation_snapshot.return_value = [
        models.PredictedGenerationValue(
            power_kilowatts=1500.0,
            valid_timestamp=frozen_time,
            location_uuid=gsp_id_map[1].uuid,
            capacity_kilowatts=10000.0,
            forecaster_name="blend",
            forecaster_version="1.3.0",
            metadata={},
        ),
    ]

    with time_machine.travel(frozen_time, tick=False):
        ts_str = frozen_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        # Passing identical start and end times bypasses the 503 cache-warming block
        url = (
            f"/v0/solar/GB/gsp/forecast/all/?start_datetime_utc={ts_str}&end_datetime_utc={ts_str}"
        )
        response = await api_client.get(url)

    assert response.status_code == 200
    data = response.json()

    # Default returns full Forecast objects
    assert "forecasts" in data
    assert data["forecasts"][0]["location"]["gspId"] == 1

    # Ensure it used the bulk snapshot DB method instead of the loop
    mock_storage.get_predicted_generation_snapshot.assert_called_once()
    mock_storage.get_predicted_generation.assert_not_called()


@pytest.mark.asyncio
async def test_gsp_pvlive_all_routing_logic(
    api_client,
    mock_storage: AsyncMock,
):
    """Verifies the complex routing logic in /pvlive/all based on gsp_ids length."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

    with time_machine.travel(frozen_time, tick=False):
        # No gsp_ids: should call get_actual_generation_snapshot once
        await api_client.get("/v0/solar/GB/gsp/pvlive/all")
        mock_storage.get_actual_generation_snapshot.assert_called_once()
        mock_storage.get_actual_generation.assert_not_called()

        mock_storage.reset_mock()

        # One gsp_id: should call get_actual_generation (timeseries)
        await api_client.get("/v0/solar/GB/gsp/pvlive/all?gsp_ids=1")
        mock_storage.get_actual_generation.assert_called_once()
        mock_storage.get_actual_generation_snapshot.assert_not_called()

        mock_storage.reset_mock()

        # Multiple gsp_ids: should call get_actual_generation_snapshot in a loop
        await api_client.get("/v0/solar/GB/gsp/pvlive/all?gsp_ids=1,2")
        # Assert it was called >1 time because it loops over pd.date_range
        assert mock_storage.get_actual_generation_snapshot.call_count > 1
        mock_storage.get_actual_generation.assert_not_called()


@pytest.mark.asyncio
async def test_gsp_pvlive_all_grouping(api_client, mock_storage: AsyncMock):
    """Verifies that /pvlive/all correctly groups generation data by datetime."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

    # Request a 1-hour window, which at 30min intervals = 3 timestamps (12:00, 12:30, 13:00)
    start_time_str = frozen_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_time_str = (frozen_time + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Define a dynamic mock response that mimics the database returning data for GSP 1 & 2
    def mock_snapshot_db_call(
        *args,  # noqa: ARG001
        **kwargs,
    ):
        req_ts = kwargs.get("snapshot_timestamp_utc")
        if not isinstance(req_ts, dt.datetime):
            raise ValueError("Expected snapshot_timestamp_utc in kwargs")
        return [
            models.ActualGenerationValue(
                power_kilowatts=111.0,
                valid_timestamp=req_ts,
                location_uuid=gsp_id_map[1].uuid,
                capacity_kilowatts=1000.0,
                observer_name="pvlive_in_day",
            ),
            models.ActualGenerationValue(
                power_kilowatts=222.0,
                valid_timestamp=req_ts,
                location_uuid=gsp_id_map[2].uuid,
                capacity_kilowatts=1000.0,
                observer_name="pvlive_in_day",
            ),
        ]

    # Bind the dynamic function to the mock
    mock_storage.get_actual_generation_snapshot.side_effect = mock_snapshot_db_call

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get(
            f"/v0/solar/GB/gsp/pvlive/all?gsp_ids=1,2&start_datetime_utc={start_time_str}&end_datetime_utc={end_time_str}",
        )

    assert response.status_code == 200
    data = response.json()

    # Assert that the date_range loop produced exactly 3 grouped timestamps
    assert len(data) == 3

    # Assert the structure of the first timestamp group (12:00)
    assert data[0]["datetimeUtc"] == "2026-01-01T12:00:00Z"
    assert "generationKwByGspId" in data[0]

    # Assert that both GSPs were correctly mapped into the dictionary for this timestamp
    gen_dict_0 = data[0]["generationKwByGspId"]
    assert gen_dict_0["1"] == 111.0
    assert gen_dict_0["2"] == 222.0

    # Assert the second timestamp group successfully progressed (12:30)
    assert data[1]["datetimeUtc"] == "2026-01-01T12:30:00Z"
    gen_dict_1 = data[1]["generationKwByGspId"]
    assert gen_dict_1["1"] == 111.0

    # Verify the database was actually called 3 times by the router's internal loop
    assert mock_storage.get_actual_generation_snapshot.call_count == 3
