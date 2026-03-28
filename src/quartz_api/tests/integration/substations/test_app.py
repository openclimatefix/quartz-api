"""Integration tests for the substations routes."""

import uuid

import pandas as pd
import pytest


# 1. Does the app start up, and can we use health
@pytest.mark.asyncio(loop_scope="session")
async def test_app_start(api_client_substations) -> None:
    """Test that the app starts up correctly with the given configuration."""
    response = await api_client_substations.get("/health")
    assert response.status_code == 200


# 2.1 Test listing all substations
@pytest.mark.asyncio(loop_scope="session")
async def test_get_substations(
    api_client_substations,
    substation_locations,  # noqa: ARG001 - ensures substations are created
) -> None:
    """Test GET /substations returns all substations."""
    response = await api_client_substations.get("/substations")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 5  # We created 5 substations in total

    # Verify the structure of each substation
    for substation in data:
        assert "substation_uuid" in substation
        assert "substation_name" in substation
        assert "substation_type" in substation
        assert "latitude" in substation
        assert "longitude" in substation
        assert "capacity_kW" in substation
        assert "metadata" in substation
        assert substation["substation_type"] == "primary"


# 2.2 Test listing substations with correct names
@pytest.mark.asyncio(loop_scope="session")
async def test_get_substations_names(
    api_client_substations,
    substation_locations,  # noqa: ARG001 - ensures substations are created
) -> None:
    """Test GET /substations returns substations with correct names."""
    response = await api_client_substations.get("/substations")
    assert response.status_code == 200

    data = response.json()
    names = {s["substation_name"] for s in data}

    expected_names = {
        "substation_1a",
        "substation_1b",
        "substation_2a",
        "substation_2b",
        "substation_3a",
    }
    assert names == expected_names


# 3.1 Test getting a specific substation by UUID
@pytest.mark.asyncio(loop_scope="session")
async def test_get_substation_by_uuid(
    api_client_substations,
    substation_locations,
) -> None:
    """Test GET /substations/{uuid} returns the correct substation."""
    # Get the first substation UUID from our fixtures
    substation_uuid, name, gsp_id = substation_locations[0]

    response = await api_client_substations.get(f"/substations/{substation_uuid}")
    assert response.status_code == 200

    data = response.json()
    assert data["substation_name"] == name
    assert data["substation_type"] == "primary"
    assert data["metadata"]["gsp_id"] == str(gsp_id)
    assert "latitude" in data
    assert "longitude" in data
    assert "capacity_kW" in data


# 3.2 Test getting a substation with unknown UUID returns 404
@pytest.mark.asyncio(loop_scope="session")
async def test_get_substation_by_uuid_not_found(
    api_client_substations,
    substation_locations,  # noqa: ARG001 - ensures substations are created
) -> None:
    """Test GET /substations/{uuid} returns 404 for unknown UUID."""
    unknown_uuid = uuid.uuid4()
    response = await api_client_substations.get(f"/substations/{unknown_uuid}")
    assert response.status_code == 404


# 3.3 Test getting each substation by UUID returns correct data
@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_substations_by_uuid(
    api_client_substations,
    substation_locations,
) -> None:
    """Test that each substation can be retrieved by its UUID."""
    for substation_uuid, name, gsp_id in substation_locations:
        response = await api_client_substations.get(f"/substations/{substation_uuid}")
        assert response.status_code == 200

        data = response.json()
        assert data["substation_name"] == name
        assert data["metadata"]["gsp_id"] == str(gsp_id)


# 4.1 Test getting forecast for a specific substation
@pytest.mark.asyncio(loop_scope="session")
async def test_get_substation_forecast(
    api_client_substations,
    substation_locations,
    make_gsp_forecast_values,  # noqa: ARG001 - ensures forecasts are created
) -> None:
    """Test GET /substations/{uuid}/forecast returns forecast values."""
    # Get the first substation UUID
    substation_uuid, _, _ = substation_locations[0]

    response = await api_client_substations.get(f"/substations/{substation_uuid}/forecast")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0  # Should have forecast values

    # Verify the structure of each forecast value
    for forecast_value in data:
        assert "power_kW" in forecast_value
        assert "time" in forecast_value


# 4.2 Test getting forecast for multiple substations
@pytest.mark.asyncio(loop_scope="session")
async def test_get_substation_forecast_multiple(
    api_client_substations,
    substation_locations,
    make_gsp_forecast_values,  # noqa: ARG001 - ensures forecasts are created
) -> None:
    """Test that forecasts can be retrieved for all substations."""
    for substation_uuid, _, _ in substation_locations:
        response = await api_client_substations.get(f"/substations/{substation_uuid}/forecast")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        # Each substation should have forecast values since they're linked to GSPs
        assert len(data) > 0


# 5.1 Test getting all substation forecasts at one timestamp
@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_substations_forecast(
    api_client_substations,
    substation_locations,
    make_gsp_forecast_values,  # noqa: ARG001 - ensures forecasts are created
) -> None:
    """Test GET /substations/forecast returns forecasts for all substations."""
    response = await api_client_substations.get("/substations/forecast")
    assert response.status_code == 200

    data = response.json()
    assert "datetime_utc" in data
    assert "forecast_values_kW" in data
    assert isinstance(data["forecast_values_kW"], dict)

    # Should have forecasts for all 5 substations
    assert len(data["forecast_values_kW"]) == 5

    forecast_uuids = set(data["forecast_values_kW"].keys())
    expected_uuids = {str(sub_uuid) for sub_uuid, _, _ in substation_locations}
    assert forecast_uuids == expected_uuids

    for _substation_uuid, power_kw in data["forecast_values_kW"].items():
        assert isinstance(power_kw, (int, float))
        assert power_kw >= 0


# 5.2 Test getting all substation forecasts at a specific timestamp
@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_substations_forecast_with_timestamp(
    api_client_substations,
    substation_locations,  # noqa arg001
    make_gsp_forecast_values,  # noqa: ARG001 - ensures forecasts are created
) -> None:
    """Test GET /substations/forecast with a specific datetime_utc parameter."""

    # Use the current time rounded to 30 minutes
    timestamp = pd.Timestamp.utcnow().floor("30min").isoformat()
    response = await api_client_substations.get(
        "/substations/forecast",
        params={"datetime_utc": timestamp},
    )
    assert response.status_code == 200

    data = response.json()
    assert "datetime_utc" in data
    assert "forecast_values_kW" in data


