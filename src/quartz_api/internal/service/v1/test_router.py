"""Unit tests for the v1 API router."""


import datetime as dt
import json
import typing
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from httpx import ASGITransport, AsyncClient

from quartz_api.internal import models
from quartz_api.internal.backends.dummydb.client import StorageClient
from quartz_api.internal.middleware.auth import AuthDependency

from .router import router

_auth_dep = typing.get_args(AuthDependency)[1].dependency


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """Return a test client with DummyDB backend and no auth."""
    app = FastAPI()
    app.include_router(router)
    FastAPICache.init(InMemoryBackend(), prefix="test")
    dummy_db = StorageClient()
    app.dependency_overrides[models.get_storage_client] = lambda: dummy_db
    app.dependency_overrides[_auth_dep] = lambda: {"sub": "test|user", "permissions": []}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_client() -> AsyncClient:
    """Return a test client with ocf:admin permissions."""
    app = FastAPI()
    app.include_router(router)
    FastAPICache.init(InMemoryBackend(), prefix="test")
    dummy_db = StorageClient()
    app.dependency_overrides[models.get_storage_client] = lambda: dummy_db
    app.dependency_overrides[_auth_dep] = lambda: {
        "sub": "test|admin",
        "permissions": ["ocf:admin"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_sources(client: AsyncClient) -> None:
    resp = await client.get("/v1/sources")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert "solar" in names


@pytest.mark.anyio
async def test_get_region_types(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/GB/region-types")
    assert resp.status_code == 200
    types = {rt["type"] for rt in resp.json()}
    assert "gsp" in types
    assert "national" in types


@pytest.mark.anyio
async def test_get_region_types_has_forecast_models(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/GB/region-types")
    gsp = next(rt for rt in resp.json() if rt["type"] == "gsp")
    assert len(gsp["forecast_models"]) > 0
    model_names = [m["name"] for m in gsp["forecast_models"]]
    assert "blend" in model_names


@pytest.mark.anyio
async def test_get_generation_sources(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/GB/generation-sources")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert "pvlive_in_day" in names
    assert "pvlive_day_after" in names


@pytest.mark.anyio
async def test_unknown_country_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/XX/region-types")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Region browsing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_top_level_regions(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/regions")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_regions_by_type(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/GB/regions?region_type=gsp")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) > 0


@pytest.mark.anyio
async def test_get_regions_by_parent_id(client: AsyncClient) -> None:
    parent_id = str(uuid4())
    resp = await client.get(f"/v1/solar/GB/regions?parent_id={parent_id}")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_regions_invalid_region_type_returns_400(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/GB/regions?region_type=unknown_type")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_region_by_id(client: AsyncClient) -> None:
    region_id = str(uuid4())
    resp = await client.get(f"/v1/solar/GB/regions/{region_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    assert "capacity_kW" in body


# ---------------------------------------------------------------------------
# Per-region forecast
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_region_forecast(client: AsyncClient) -> None:
    region_id = str(uuid4())
    resp = await client.get(f"/v1/solar/GB/regions/{region_id}/forecast")
    assert resp.status_code == 200
    body = resp.json()
    assert "capacity_kW" in body
    assert "values" in body
    assert "model_name" in body
    assert "init_time" in body


@pytest.mark.anyio
async def test_get_region_forecast_last_updated(client: AsyncClient) -> None:
    region_id = str(uuid4())
    resp = await client.get(f"/v1/solar/GB/regions/{region_id}/forecast/last_updated")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Per-region generation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_region_generation(client: AsyncClient) -> None:
    region_id = str(uuid4())
    resp = await client.get(f"/v1/solar/GB/regions/{region_id}/generation")
    assert resp.status_code == 200
    body = resp.json()
    assert "capacity_kW" in body
    assert "observer_name" in body
    assert "values" in body


# ---------------------------------------------------------------------------
# Snapshot endpoints
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_forecasts_snapshot(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/GB/forecasts/snapshot?region_type=gsp")
    assert resp.status_code == 200
    body = resp.json()
    assert "time" in body
    assert "values" in body


@pytest.mark.anyio
async def test_get_forecasts_snapshot_requires_region_type(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/GB/forecasts/snapshot")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_generation_snapshot(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/GB/generation/snapshot?region_type=gsp")
    assert resp.status_code == 200
    body = resp.json()
    assert "time" in body
    assert "observer_name" in body
    assert "values" in body


@pytest.mark.anyio
async def test_get_generation_snapshot_requires_region_type(client: AsyncClient) -> None:
    resp = await client.get("/v1/solar/GB/generation/snapshot")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_forecasts_snapshot_national_fallback(client: AsyncClient) -> None:
    """National snapshot uses get_predicted_generation (not get_predicted_generation_snapshot)."""
    resp = await client.get("/v1/solar/GB/forecasts/snapshot?region_type=national")
    assert resp.status_code == 200
    body = resp.json()
    assert "time" in body
    assert "values" in body


@pytest.mark.anyio
async def test_get_forecasts_snapshot_invalid_region_type_returns_400(
    client: AsyncClient,
) -> None:
    resp = await client.get("/v1/solar/GB/forecasts/snapshot?region_type=unknown_type")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_forecasts_timeseries_invalid_region_type_returns_400(
    client: AsyncClient,
) -> None:
    resp = await client.get("/v1/solar/GB/forecasts/timeseries?region_type=unknown_type")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Timeseries (matrix) endpoints — cold cache → 503
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_forecasts_timeseries_cold_cache_returns_503(client: AsyncClient) -> None:
    """Timeseries endpoint must 503 when cache has not been pre-warmed."""
    FastAPICache.init(InMemoryBackend(), prefix="cold")
    resp = await client.get("/v1/solar/GB/forecasts/timeseries?region_type=gsp")
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers


@pytest.mark.anyio
async def test_get_generation_timeseries_cold_cache_returns_503(client: AsyncClient) -> None:
    FastAPICache.init(InMemoryBackend(), prefix="cold2")
    resp = await client.get("/v1/solar/GB/generation/timeseries?region_type=gsp")
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers


@pytest.mark.anyio
async def test_get_forecasts_timeseries_warm_cache(client: AsyncClient) -> None:
    """Timeseries endpoint returns ForecastMatrix (may have empty regions) when _meta is cached."""
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    now = dt.datetime.now(tz=dt.UTC)
    meta = {
        "model_name": "blend",
        "model_version": "1.0.0",
        "created_time": now.isoformat(),
        "init_time": now.isoformat(),
    }
    await backend.set(
        f"{prefix}:v1:timeseries:solar:GB:gsp:_meta", json.dumps(meta), expire=3600,
    )

    resp = await client.get("/v1/solar/GB/forecasts/timeseries?region_type=gsp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_name"] == "blend"
    assert "regions" in body


@pytest.mark.anyio
async def test_get_generation_timeseries_warm_cache(client: AsyncClient) -> None:
    """Generation timeseries returns GenerationMatrix when _meta is cached."""
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    observer = "pvlive_in_day"
    meta = {"observer_name": observer}
    await backend.set(
        f"{prefix}:v1:timeseries:generation:solar:GB:gsp:{observer}:_meta",
        json.dumps(meta),
        expire=3600,
    )

    resp = await client.get(
        f"/v1/solar/GB/generation/timeseries?region_type=gsp&observer={observer}",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["observer_name"] == observer
    assert "regions" in body


@pytest.mark.anyio
async def test_get_forecasts_timeseries_region_ids_filter(client: AsyncClient) -> None:
    """region_ids filter reduces result to matching regions only."""
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    now = dt.datetime.now(tz=dt.UTC)
    meta = {
        "model_name": "blend",
        "model_version": "1.0.0",
        "created_time": now.isoformat(),
        "init_time": now.isoformat(),
    }
    await backend.set(
        f"{prefix}:v1:timeseries:solar:GB:gsp:_meta", json.dumps(meta), expire=3600,
    )

    # UUID that will not match any region returned by DummyDB
    non_existent_id = str(uuid4())
    resp = await client.get(
        f"/v1/solar/GB/forecasts/timeseries?region_type=gsp&region_ids={non_existent_id}",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["regions"] == []


# ---------------------------------------------------------------------------
# Admin refresh endpoints
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_refresh_forecasts_requires_admin(client: AsyncClient) -> None:
    resp = await client.post("/v1/solar/GB/forecasts/refresh?region_type=gsp")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_refresh_forecasts_as_admin(admin_client: AsyncClient) -> None:
    resp = await admin_client.post("/v1/solar/GB/forecasts/refresh?region_type=gsp")
    assert resp.status_code == 202


@pytest.mark.anyio
async def test_refresh_generation_requires_admin(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/solar/GB/generation/refresh?region_type=gsp&observer=pvlive_in_day",
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_refresh_generation_as_admin(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        "/v1/solar/GB/generation/refresh?region_type=gsp&observer=pvlive_in_day",
    )
    assert resp.status_code == 202
