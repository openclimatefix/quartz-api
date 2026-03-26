import pandas as pd
import pytest


# 1. Does the app start up, and can we use health
@pytest.mark.asyncio(loop_scope="session")
async def test_app_start(api_client_uk_national) -> None:
    """Test that the app starts up correctly with the given configuration."""

    response = await api_client_uk_national.get("/health")
    assert response.status_code == 200


# 2.1 Test the National Forecast route
@pytest.mark.asyncio(loop_scope="session")
async def test_national_forecast(
    api_client_uk_national,
    national_location,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_national_forecast_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""

    response = await api_client_uk_national.get("/v0/solar/GB/national/forecast")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 24


# 2.2 Test the National Forecast, with horizon minutes
@pytest.mark.asyncio(loop_scope="session")
async def test_national_forecast_horizon_minutes(
    api_client_uk_national,
    national_location,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_national_forecast_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data.

    We are testing for forecast_horizon of 120 minutes
    """

    response = await api_client_uk_national.get(
        "/v0/solar/GB/national/forecast?forecast_horizon_minutes=120",
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 16


# 2.2 Test the National Forecast, include metadata
@pytest.mark.asyncio(loop_scope="session")
async def test_national_forecast_include_metadata(
    api_client_uk_national,
    national_location,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_national_forecast_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""

    response = await api_client_uk_national.get(
        "/v0/solar/GB/national/forecast?include_metadata=true",
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "location" in data
    assert "forecastValues" in data
    assert len(data["forecastValues"]) == 24


# 2.3 Test the National Forecast, metadata and non-metadata values are the same
@pytest.mark.asyncio(loop_scope="session")
async def test_national_forecast_metadata_true_and_false(
    api_client_uk_national,
    national_location,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_national_forecast_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""

    now = pd.Timestamp.utcnow().floor("30min").to_pydatetime().strftime("%Y-%m-%dT%H:%M:%SZ")

    url = f"/v0/solar/GB/national/forecast?start_datetime_utc={now}"
    response = await api_client_uk_national.get(url)
    assert response.status_code == 200
    data = response.json()

    url = f"/v0/solar/GB/national/forecast?include_metadata=true&start_datetime_utc={now}"
    response_with_metadata = await api_client_uk_national.get(url)

    assert response_with_metadata.status_code == 200
    data_with_metadata = response_with_metadata.json()

    assert len(data_with_metadata["forecastValues"]) == len(data)
    for i in range(10):
        assert data[i]["targetTime"] == data_with_metadata["forecastValues"][i]["targetTime"]
        assert (
            data[i]["expectedPowerGenerationMegawatts"]
            == data_with_metadata["forecastValues"][i]["expectedPowerGenerationMegawatts"]
        )
    assert data_with_metadata["model"]["version"] == "1.2.3"


# 3.1 Test the National PVlive route
@pytest.mark.asyncio(loop_scope="session")
async def test_national_pvlive(
    api_client_uk_national,
    national_location,  # noqa arg001
    make_observers,  # noqa arg001
    make_national_observation_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National PVlive data."""

    response = await api_client_uk_national.get("/v0/solar/GB/national/pvlive")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert "datetimeUtc" in data[0]
    assert "solarGenerationKw" in data[0]

    # lets make sure the top results is the latest timestamp
    assert data[0]["datetimeUtc"] > data[-1]["datetimeUtc"]


# 3.2 Test the National PVlive route
@pytest.mark.asyncio(loop_scope="session")
async def test_national_pvlive_day_after(
    api_client_uk_national,
    national_location,  # noqa arg001
    make_observers,  # noqa arg001
    make_national_observation_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National PVlive data."""

    response = await api_client_uk_national.get("/v0/solar/GB/national/pvlive?regime=day_after")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


# 4.1 Check GSP forecast route
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""
    response = await api_client_uk_national.get("/v0/solar/GB/gsp/1/forecast")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 10


# 4.1.1 Check GSP forecast route - no gsp location
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_no_location(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""

    response = await api_client_uk_national.get("/v0/solar/GB/gsp/100/forecast")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


# 4.2 Check GSP pvlive route
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_pvlive(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_observers,  # noqa arg001
    make_gsp_observation_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""

    response = await api_client_uk_national.get("/v0/solar/GB/gsp/1/pvlive")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 10


# 4.3 Check GSP forecast route — default (compact=false) returns one Forecast per GSP
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_all(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
) -> None:
    """Test that /forecast/all/ returns one Forecast object per GSP with multiple timesteps."""

    response = await api_client_uk_national.get("/v0/solar/GB/gsp/forecast/all/?compact=true")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 1  # multiple timesteps
    assert "datetimeUtc" in data[0]
    assert "forecastValues" in data[0]
    assert len(data[0]["forecastValues"]) >= 1  # multiple values per timestep



# 4.3.1 Check GSP forecast route with gsp_ids filter (compact mode)
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_all_gsp_ids(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
) -> None:
    """Test gsp_ids filter with compact=true returns only requested GSPs at all timesteps."""

    url = "/v0/solar/GB/gsp/forecast/all/?gsp_ids=1,2,3&compact=true"
    response = await api_client_uk_national.get(url)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 10
    assert "datetimeUtc" in data[0]
    assert "forecastValues" in data[0]
    assert len(data[0]["forecastValues"]) == 3


# 4.3.2 Check GSP forecast route, compact=false
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_compact_false(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""

    response = await api_client_uk_national.get("/v0/solar/GB/gsp/forecast/all/")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 10
    assert "location" in data[0]
    assert "model" in data[0]
    assert "forecastValues" in data[0]
    assert len(data[0]["forecastValues"]) == 10

# 4.3.3 Check GSP forecast route, compact=false, and restrict gsps
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_compact_false_gsp_ids(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""

    response = await api_client_uk_national.get("/v0/solar/GB/gsp/forecast/all/?gsp_ids=1,2,3")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 3
    assert "location" in data[0]
    assert "model" in data[0]
    assert "forecastValues" in data[0]
    assert len(data[0]["forecastValues"]) == 10


# 4.3.4 Cache refresh endpoint returns 202 with valid token
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_all_refresh(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
) -> None:
    """Test that the cache refresh endpoint returns 202 with a valid token."""
    import os
    os.environ["CACHE_REFRESH_TOKEN"] = "test-secret"

    response = await api_client_uk_national.post(
        "/v0/solar/GB/gsp/forecast/all/refresh",
        headers={"X-Refresh-Token": "test-secret"},
    )
    assert response.status_code == 202


# 4.3.5 Cache refresh endpoint rejects wrong token
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_all_refresh_wrong_token(
    api_client_uk_national,
) -> None:
    """Test that the cache refresh endpoint rejects an invalid token."""
    response = await api_client_uk_national.post(
        "/v0/solar/GB/gsp/forecast/all/refresh",
        headers={"X-Refresh-Token": "wrong-token"},
    )
    assert response.status_code == 403


# 4.4 Check GSP pvlive route
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_pvlive_all(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""

    # Calling with no specified gsps only returns a single snapshot timestamp
    response = await api_client_uk_national.get("/v0/solar/GB/gsp/pvlive/all")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1

    # Calling with specified gsps returns all timestamps for those gsps
    response = await api_client_uk_national.get("/v0/solar/GB/gsp/pvlive/all?gsp_ids=1,2,3")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 10


# 5.1 check system routes
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_system(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    national_location,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""
    response = await api_client_uk_national.get("/v0/system/GB/gsp/")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 11


# 6.1 check status routes
# TODO need to add sqlite database
# @pytest.mark.asyncio(loop_scope="session")
# async def test_gsp_status(
#     api_client_uk_national,
#     gsp_locations,
# ) -> None:
#     """Test a sample endpoint for UK National forecast data."""

#     response = await api_client_uk_national.get("/v0/solar/GB/status")
#     assert response.status_code == 200
#     data = response.json()
#     assert isinstance(data, list)
#     assert len(data) == 11


# 6.2 check status routes
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_status_check_last_forecast_run(
    api_client_uk_national,
    national_location,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_national_forecast_values,  # noqa arg001
) -> None:
    """Test a sample endpoint for UK National forecast data."""

    response = await api_client_uk_national.get("/v0/solar/GB/status/check_last_forecast_run")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, str)

@pytest.mark.asyncio(loop_scope="session")
async def test_system_details(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    national_location,  # noqa arg001
) -> None:
    """Test a sample endpoint for system details."""

    response = await api_client_uk_national.get("/v0/system/GB/gsp/?gsp_id=0")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1

