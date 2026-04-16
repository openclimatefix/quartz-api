import datetime as dt
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import time_machine

from quartz_api.internal import models

# Create fake location objects
FAKE_SUB_1 = models.Location(
    uuid=uuid4(),
    name="Substation Alpha",
    latitude=51.0,
    longitude=-1.0,
    capacity_kilowatts=100.0,
    metadata={"gsp_id": 1},
)
FAKE_SUB_2 = models.Location(
    uuid=uuid4(),
    name="Substation Beta",
    latitude=52.0,
    longitude=-2.0,
    capacity_kilowatts=250.0,
    metadata={"gsp_id": 1},
)
FAKE_GSP_1 = models.Location(
    uuid=uuid4(),
    name="GSP One",
    latitude=51.5,
    longitude=-1.5,
    capacity_kilowatts=1000.0,
    metadata={"gsp_id": 1},
)


def mock_get_locations_side_effect(
    *args,  # noqa: ARG001
    **kwargs,
):
    """Dynamic mock to return different locations based on the requested location_type."""
    loc_type = kwargs.get("location_type")
    loc_uuid = kwargs.get("location_uuid")

    if loc_type == models.LocationType.SUBSTATION:
        if loc_uuid == FAKE_SUB_1.uuid:
            return [FAKE_SUB_1]
        elif loc_uuid:
            return []  # 404 case
        return [FAKE_SUB_1, FAKE_SUB_2]

    elif loc_type == models.LocationType.GSP:
        return [FAKE_GSP_1]

    return []


@pytest.mark.asyncio
async def test_get_substations(api_client, mock_storage: AsyncMock):
    """Verifies the /substations list route maps data correctly."""
    mock_storage.get_locations.side_effect = mock_get_locations_side_effect

    response = await api_client.get("/substations")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["substation_uuid"] == str(FAKE_SUB_1.uuid)
    assert data[0]["substation_name"] == "Substation Alpha"
    assert data[0]["capacity_kW"] == 100.0

    mock_storage.get_locations.assert_called_once()
    assert (
        mock_storage.get_locations.call_args.kwargs["location_type"]
        == models.LocationType.SUBSTATION
    )


@pytest.mark.asyncio
async def test_get_substation_by_uuid_valid(api_client, mock_storage: AsyncMock):
    """Verifies a single substation fetch works."""
    mock_storage.get_locations.side_effect = mock_get_locations_side_effect

    response = await api_client.get(f"/substations/{FAKE_SUB_1.uuid}")

    assert response.status_code == 200
    data = response.json()
    # Note: The router returns SubstationProperties here, which lacks the UUID field
    assert data["substation_name"] == "Substation Alpha"
    assert data["capacity_kW"] == 100.0


@pytest.mark.asyncio
async def test_get_substation_by_uuid_404(api_client, mock_storage: AsyncMock):
    """Verifies a missing substation properly raises a 404."""
    mock_storage.get_locations.side_effect = mock_get_locations_side_effect
    random_uuid = uuid4()

    response = await api_client.get(f"/substations/{random_uuid}")

    assert response.status_code == 404
    assert f"Substation with UUID {random_uuid} not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_substation_forecast(api_client, mock_storage: AsyncMock):
    """Verifies formatting and timezone conversion for a specific substation forecast."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

    mock_storage.get_predicted_generation.return_value = [
        models.PredictedGenerationValue(
            power_kilowatts=42.5,
            valid_timestamp=frozen_time + dt.timedelta(hours=1),
            location_uuid=FAKE_SUB_1.uuid,
            capacity_kilowatts=100.0,
            forecaster_name="blend",
            forecaster_version="1.3.0",
            created_timestamp=frozen_time,
            init_timestamp=frozen_time - dt.timedelta(minutes=30),
            plevels_kilowatts={"p10": 30.0, "p90": 50.0},
        ),
    ]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get(
            f"/substations/{FAKE_SUB_1.uuid}/forecast",
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    assert data[0]["power_kW"] == 42.5
    assert "forecaster_name" not in data[0]  # Should be excluded from response
    assert data[0]["plevel_kW"]["p10"] == 30.0

    # Assert DB was called with correct parameters (time windows calculated from now)
    mock_storage.get_predicted_generation.assert_called_once()
    kwargs = mock_storage.get_predicted_generation.call_args.kwargs
    assert kwargs["location_uuid"] == FAKE_SUB_1.uuid


@pytest.mark.asyncio
async def test_get_all_substations_forecast_scaling(
    api_client,
    mock_storage: AsyncMock,
):
    """Verifies the proportional capacity math scales GSP power correctly."""
    frozen_time = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

    mock_storage.get_locations.side_effect = mock_get_locations_side_effect
    mock_storage.get_predicted_generation_snapshot.return_value = [
        models.PredictedGenerationValue(
            power_kilowatts=5000.0,
            created_timestamp=frozen_time - dt.timedelta(minutes=60),
            init_timestamp=frozen_time - dt.timedelta(minutes=30),
            plevels_kilowatts={"p10": 4000.0, "p90": 6000.0},
            valid_timestamp=frozen_time,
            location_uuid=FAKE_GSP_1.uuid,
            capacity_kilowatts=10000.0,
            forecaster_name="blend",
            forecaster_version="1.3.0",
        ),
    ]

    with time_machine.travel(frozen_time, tick=False):
        response = await api_client.get("/substations/forecast")

    assert response.status_code == 200
    data = response.json()

    # Check top-level structure
    assert data["datetime_utc"] == "2026-01-01T12:00:00Z"
    forecast_values = data["forecast_values_kW"]

    # GSP 1 Capacity: 10000 kW. GSP 1 Output: 5000 kW.
    # Substation 1 Capacity: 100 kW. (100 / 10000) = 0.01 ratio. 5000 * 0.01 = 50 kW.
    assert forecast_values[str(FAKE_SUB_1.uuid)] == 500.0

    # Substation 2 Capacity: 250 kW. (250 / 10000) = 0.025 ratio. 5000 * 0.025 = 125 kW.
    assert forecast_values[str(FAKE_SUB_2.uuid)] == 1250.0

    # Verify db calls
    assert mock_storage.get_locations.call_count == 2
    mock_storage.get_predicted_generation_snapshot.assert_called_once()
    snapshot_kwargs = mock_storage.get_predicted_generation_snapshot.call_args.kwargs
    # Ensure it only requested the snapshot for the relevant GSP UUIDs
    assert snapshot_kwargs["location_uuids"] == [FAKE_GSP_1.uuid]
