"""Integration tests for the substations routes (Plumbing Only)."""

import pandas as pd
import pytest


@pytest.mark.asyncio(loop_scope="session")
async def test_app_start(api_client_dataplatform) -> None:
    """Test that the app starts up correctly and Healthcheck passes."""
    response = await api_client_dataplatform.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio(loop_scope="session")
async def test_get_substations(
    api_client_dataplatform,
    substation_locations,  # noqa: ARG001 - ensures substations are created in DB
) -> None:
    """Test GET /substations connects to DB and returns data."""
    response = await api_client_dataplatform.get("/substations")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # We seeded 5 substations in the DB
    assert len(data) == 5


@pytest.mark.asyncio(loop_scope="session")
async def test_get_substation_by_uuid(
    api_client_dataplatform,
    substation_locations,
) -> None:
    """Test GET /substations/{uuid} connects to DB."""
    # Just grab the first UUID from the seeded DB
    substation_uuid = substation_locations[0][0]

    response = await api_client_dataplatform.get(f"/substations/{substation_uuid}")

    assert response.status_code == 200
    assert "capacity_kW" in response.json()


@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_substations_forecast(
    api_client_dataplatform,
    substation_locations,  # noqa: ARG001
    make_gsp_forecast_values,  # noqa: ARG001 - ensures GSP forecasts are in DB
) -> None:
    """Test GET /substations/forecast connects to DB."""

    # We use a timestamp close to "now" to ensure it matches the seeded data window
    timestamp = pd.Timestamp.utcnow().floor("30min").isoformat()

    response = await api_client_dataplatform.get(
        "/substations/forecast",
        params={"datetime_utc": timestamp},
    )

    assert response.status_code == 200
    data = response.json()
    assert "datetime_utc" in data
    assert "forecast_values_kW" in data
    assert isinstance(data["forecast_values_kW"], dict)
