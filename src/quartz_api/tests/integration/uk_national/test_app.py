import pandas as pd
import pytest
from fastapi_cache import FastAPICache


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


# 4.3 Default /forecast/all/ (no gsp_ids) returns 503 with Retry-After on cache miss
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_all_cache_miss(
    api_client_uk_national,
) -> None:
    """Cache miss on /forecast/all/ should return 503 with Retry-After: 60."""
    for url in [
        "/v0/solar/GB/gsp/forecast/all/",
        "/v0/solar/GB/gsp/forecast/all/?compact=true",
    ]:
        response = await api_client_uk_national.get(url)
        assert response.status_code == 503
        assert response.headers.get("retry-after") == "60"


# 4.3.0 Populated cache returns 200
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_all_cache_hit(
    api_client_uk_national,
) -> None:
    """Pre-populating the cache should result in a 200 rather than a 503."""
    backend = FastAPICache.get_backend()
    prefix = FastAPICache.get_prefix()
    base_key = f"{prefix}::get:/v0/solar/GB/gsp/forecast/all/:"

    cached_forecast = b'{"forecasts":[{"location": {"label": "GSP 1", "gspId": 1}, "model": {"name": "blend", "version": "1.3.0"}, "forecastValues": [{"targetTime": "2026-01-01T00:00:00Z", "expectedPowerGenerationMegawatts": 1.23}], "inputDataLastUpdated": {"gsp": "2026-01-01T00:00:00Z", "nwp": "2026-01-01T00:00:00Z", "pv": "2026-01-01T00:00:00Z", "satellite": "2026-01-01T00:00:00Z"}}]}'  # noqa: E501
    cached_compact = b'[{"datetimeUtc": "2026-01-01T00:00:00Z", "forecastValues": {"1": 1.23}}]'
    await backend.set(f"{base_key}[]:[]", cached_forecast, expire=60)
    await backend.set(f"{base_key}[('compact', 'true')]:[]", cached_compact, expire=60)

    response = await api_client_uk_national.get("/v0/solar/GB/gsp/forecast/all/")
    assert response.status_code == 200
    data = response.json()
    data = data["forecasts"]
    assert data[0]["model"]["name"] == "blend"
    assert data[0]["forecastValues"][0]["expectedPowerGenerationMegawatts"] == 1.23

    response = await api_client_uk_national.get("/v0/solar/GB/gsp/forecast/all/?compact=true")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["forecastValues"]["1"] == 1.23


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
    assert len(data) == 9
    assert "datetimeUtc" in data[0]
    assert "forecastValues" in data[0]
    assert len(data[0]["forecastValues"]) == 3


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
    data = data["forecasts"]
    assert isinstance(data, list)
    assert len(data) == 3
    assert "location" in data[0]
    assert "model" in data[0]
    assert "forecastValues" in data[0]
    assert len(data[0]["forecastValues"]) == 9


# 4.3.4 Cache refresh endpoint returns 202 for ocf:admin user
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_all_refresh(
    api_client_uk_national_admin,
) -> None:
    """Test that the cache refresh endpoint returns 202 for an admin user."""
    response = await api_client_uk_national_admin.post(
        "/v0/solar/GB/gsp/forecast/all/refresh",
    )
    assert response.status_code == 202


# 4.3.5 Cache refresh endpoint returns 403 for non-admin user
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_all_refresh_non_admin(
    api_client_uk_national_non_admin,
) -> None:
    """Test that the cache refresh endpoint rejects a non-admin user."""
    response = await api_client_uk_national_non_admin.post(
        "/v0/solar/GB/gsp/forecast/all/refresh",
    )
    assert response.status_code == 403


# 4.3.6 Get forecast/all with for one timestamp
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_all_for_one_timestamp(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
) -> None:
    """Test that the cache refresh endpoint rejects a non-admin user."""
    now = (pd.Timestamp.utcnow().ceil("30min").to_pydatetime()).strftime("%Y-%m-%dT%H:00:00Z")
    response = await api_client_uk_national.get(
        f"/v0/solar/GB/gsp/forecast/all/?start_datetime_utc={now}&end_datetime_utc={now}",
    )
    assert response.status_code == 200
    data = response.json()
    data = data["forecasts"]
    assert isinstance(data, list)
    assert len(data) == 10 # 10 gsps

# 4.3.7 Get forecast/all with for one timestamp, compact=true
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_forecast_all_for_one_timestamp_compact(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
) -> None:
    """Test that the cache refresh endpoint rejects a non-admin user."""
    now = (pd.Timestamp.utcnow().ceil("30min").to_pydatetime()).strftime("%Y-%m-%dT%H:00:00Z")
    response = await api_client_uk_national.get(
        f"/v0/solar/GB/gsp/forecast/all/?start_datetime_utc={now}&end_datetime_utc={now}&compact=true",
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1





# 4.4 Check GSP pvlive route
@pytest.mark.asyncio(loop_scope="session")
async def test_gsp_pvlive_all(
    api_client_uk_national,
    gsp_locations,  # noqa arg001
    make_forecasters,  # noqa arg001
    make_gsp_forecast_values,  # noqa arg001
    make_observers,  # noqa arg001
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

    response = await api_client_uk_national.get("/v0/solar/GB/check_last_forecast_run")
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

