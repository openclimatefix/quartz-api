import datetime as dt
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import time_machine
from fastapi_cache import FastAPICache

from quartz_api.internal import models
from quartz_api.internal.service.uk_national import status_router
from quartz_api.internal.service.uk_national.endpoint_types import gsp_id_map


@pytest_asyncio.fixture(autouse=True)
async def clear_cache():
    """FastAPICache.init() no-ops once initialised, so the store is shared between
    tests. Clear it so cached routes don't serve a previous test's response."""
    await FastAPICache.clear()
    yield
    await FastAPICache.clear()


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



def _mock_status_api(monkeypatch, handler):
    """Route the status router's outbound httpx calls to an in-process handler."""
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


@pytest.mark.asyncio
async def test_get_status_returns_upstream_status(api_client, monkeypatch):
    """Verifies status and message are proxied from the Quartz Status API."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "key": "gb-solar",
                "name": "GB Solar",
                "status": "warning",
                "message": "Degraded",
                "source": "manual",
                "updatedAt": "2026-07-21T16:45:16.252Z",
            },
        )

    _mock_status_api(monkeypatch, handler)

    response = await api_client.get("/v0/solar/GB/status")

    assert response.status_code == 200
    assert response.json() == {"status": "warning", "message": "Degraded"}
    assert seen == [f"{status_router.STATUS_URL}/products/gb-solar"]


@pytest.mark.asyncio
async def test_get_status_passes_through_info(api_client, monkeypatch):
    """Verifies the Status API 0.2.0 'info' value reaches clients unchanged.

    'info' is a deliberate, non-degraded notice (planned maintenance, a heads-up)
    and is distinct from 'unknown', which means no signal at all. The v0 Status
    model types `status` as a plain str, so no mapping is needed — this test pins
    that, since narrowing the field later would silently drop the value.
    """

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200,
            json={
                "key": "gb-solar",
                "name": "GB Solar",
                "status": "info",
                "message": "Planned maintenance 02:00-03:00 UTC.",
                "source": "manual",
                "updatedAt": "2026-08-21T16:45:16.252Z",
            },
        )

    _mock_status_api(monkeypatch, handler)

    response = await api_client.get("/v0/solar/GB/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "info",
        "message": "Planned maintenance 02:00-03:00 UTC.",
    }


@pytest.mark.asyncio
async def test_get_status_null_message_becomes_empty_string(api_client, monkeypatch):
    """Verifies a null upstream message is coerced to an empty string."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"status": "ok", "message": None})

    _mock_status_api(monkeypatch, handler)

    response = await api_client.get("/v0/solar/GB/status")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": ""}


@pytest.mark.asyncio
async def test_get_status_upstream_error_degrades_to_unknown(api_client, monkeypatch):
    """Verifies an upstream 500 yields 200 with an 'unknown' status, not a 5xx."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(500)

    _mock_status_api(monkeypatch, handler)

    response = await api_client.get("/v0/solar/GB/status")

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


@pytest.mark.asyncio
async def test_get_status_connection_error_degrades_to_unknown(api_client, monkeypatch):
    """Verifies an unreachable Status API yields 200 with an 'unknown' status."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    _mock_status_api(monkeypatch, handler)

    response = await api_client.get("/v0/solar/GB/status")

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
