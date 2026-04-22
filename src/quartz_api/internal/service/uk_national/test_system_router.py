from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from quartz_api.internal import models
from quartz_api.internal.service.uk_national.endpoint_types import gsp_id_map


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
async def test_get_system_details_national_only(api_client, mock_storage: AsyncMock):
    """Verifies requesting gsp_id=0 fetches National details with correct capacity parsing."""

    mock_storage.get_locations.return_value = [
        models.Location(
            uuid=gsp_id_map[0].uuid,
            name="national",
            latitude=0.0,
            longitude=0.0,
            capacity_kilowatts=20000.0,
            # Test that the router correctly overrides capacity using metadata
            metadata={"capacity_no_degradation_kw": 25000000.0},
        ),
    ]

    response = await api_client.get("/v0/system/GB/gsp/?gsp_id=0")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    # Check the hardcoded formatting for National
    assert data[0]["gspId"] == 0
    assert data[0]["gspName"] == "National"
    assert data[0]["regionName"] == " National" # The space here is 'required'
    assert data[0]["installedCapacityMw"] == 25000.0

    # Ensure it passed the correct location_type to the DB
    mock_storage.get_locations.assert_called_once()
    kwargs = mock_storage.get_locations.call_args.kwargs
    assert kwargs["location_type"] == models.LocationType.NATION


@pytest.mark.asyncio
async def test_get_system_details_specific_gsp(api_client, mock_storage: AsyncMock):
    """Verifies requesting a specific GSP > 0 fetches from the in-memory map directly."""
    response = await api_client.get("/v0/system/GB/gsp/?gsp_id=1")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    assert data[0]["gspId"] == 1
    assert data[0]["installedCapacityMw"] == 10.0

    mock_storage.get_locations.assert_not_called()


@pytest.mark.asyncio
async def test_get_system_details_all(api_client, mock_storage: AsyncMock):
    """Verifies requesting all GSPs hits the DB twice and sorts the output."""

    def mock_get_locations_side_effect(
            *args, # noqa: ARG001
            **kwargs,
        ):
        loc_type = kwargs.get("location_type")
        if loc_type == models.LocationType.NATION:
            return [
                models.Location(
                    uuid=gsp_id_map[0].uuid, name="national", latitude=0, longitude=0,
                    capacity_kilowatts=20000.0, metadata={},
                ),
            ]
        elif loc_type == models.LocationType.GSP:
            # Return GSPs out of order to ensure the router's sorting logic works
            return [
                models.Location(
                    uuid=uuid4(), name="gsp_2", latitude=0, longitude=0,
                    capacity_kilowatts=15000.0, metadata={"gsp_id": 2},
                ),
                models.Location(
                    uuid=uuid4(), name="gsp_1", latitude=0, longitude=0,
                    capacity_kilowatts=10000.0, metadata={"gsp_id": 1},
                ),
            ]
        return []

    mock_storage.get_locations.side_effect = mock_get_locations_side_effect

    response = await api_client.get("/v0/system/GB/gsp/")

    assert response.status_code == 200
    data = response.json()

    assert len(data) == 3

    assert data[0]["gspId"] == 0
    assert data[1]["gspId"] == 1
    assert data[2]["gspId"] == 2

    # Verify the DB was called exactly twice (once for National, once for bulk GSPs)
    assert mock_storage.get_locations.call_count == 2
