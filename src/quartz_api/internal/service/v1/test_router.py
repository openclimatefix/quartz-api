"""Unit tests for the v1 API router."""

# ruff: noqa: ARG002

import datetime as dt
import json
import typing
from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

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

# Fixed UUID used in tests that need to control which region UUID is in the cache.
_FIXED_GSP_UUID = uuid4()


class FixedUUIDStorageClient(StorageClient):
    """DummyDB variant that returns a stable UUID for GSP locations.

    Allows tests to pre-populate per-region cache keys and verify window/filter logic.
    """

    async def get_locations(  # type: ignore[override]
        self,
        energy_type: models.EnergyType,
        location_type: models.LocationType | None,
        authdata: dict,
        location_uuid: UUID | None = None,
        enclosing_location_uuid: UUID | None = None,
    ) -> list[models.Location]:
        if location_type == models.LocationType.GSP:
            return [
                models.Location(
                    uuid=_FIXED_GSP_UUID,
                    name="Fixed GSP",
                    latitude=51.0,
                    longitude=-1.0,
                    capacity_kilowatts=76000,
                    location_type=models.LocationType.GSP,
                ),
            ]
        return await super().get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata=authdata,
            location_uuid=location_uuid,
            enclosing_location_uuid=enclosing_location_uuid,
        )


class NullLocationsForUUIDClient(StorageClient):
    """Returns an empty list for any untyped location lookup that filters by UUID.

    Simulates "region not found in the data platform" for 404 path tests.
    """

    async def get_locations(  # type: ignore[override]
        self,
        energy_type: models.EnergyType,
        location_type: models.LocationType | None,
        authdata: dict,
        location_uuid: UUID | None = None,
        enclosing_location_uuid: UUID | None = None,
    ) -> list[models.Location]:
        if location_type is None and location_uuid is not None:
            return []
        return await super().get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata=authdata,
            location_uuid=location_uuid,
            enclosing_location_uuid=enclosing_location_uuid,
        )


class NationResponseClient(StorageClient):
    """Returns a NATION-type location for untyped lookups with a specific UUID.

    Used to test model validation for national region types (e.g. blend_adjust).
    """

    async def get_locations(  # type: ignore[override]
        self,
        energy_type: models.EnergyType,
        location_type: models.LocationType | None,
        authdata: dict,
        location_uuid: UUID | None = None,
        enclosing_location_uuid: UUID | None = None,
    ) -> list[models.Location]:
        if location_type is None and location_uuid is not None:
            return [
                models.Location(
                    uuid=location_uuid,
                    name="uk",
                    latitude=54.0,
                    longitude=-2.0,
                    capacity_kilowatts=15_000_000,
                    location_type=models.LocationType.NATION,
                ),
            ]
        return await super().get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata=authdata,
            location_uuid=location_uuid,
            enclosing_location_uuid=enclosing_location_uuid,
        )


class NationNameStorageClient(StorageClient):
    """DummyDB variant that returns a configurable nation name for NATION-type lookups.

    Needed for non-GB countries whose nation_name differs from DummyDB's hardcoded "uk".
    """

    def __init__(self, nation_name: str) -> None:
        super().__init__()
        self._nation_name = nation_name

    async def get_locations(  # type: ignore[override]
        self,
        energy_type: models.EnergyType,
        location_type: models.LocationType | None,
        authdata: dict,
        location_uuid: UUID | None = None,
        enclosing_location_uuid: UUID | None = None,
    ) -> list[models.Location]:
        nation = models.Location(
            uuid=location_uuid or uuid4(),
            name=self._nation_name,
            latitude=52.0,
            longitude=5.0,
            capacity_kilowatts=10_000_000,
            location_type=models.LocationType.NATION,
        )
        if location_type == models.LocationType.NATION:
            return [nation]
        # Untyped UUID lookup — return nation so routes see the correct LocationType.
        if location_type is None and location_uuid is not None:
            return [nation]
        return await super().get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata=authdata,
            location_uuid=location_uuid,
            enclosing_location_uuid=enclosing_location_uuid,
        )


class EmptyForecastClient(StorageClient):
    """Returns an empty list for get_predicted_generation.

    Simulates no-forecast-found responses (e.g. for last-updated 404 tests).
    """

    async def get_predicted_generation(  # type: ignore[override]
        self,
        location_uuid: UUID | str,
        window_start: dt.datetime,
        window_end: dt.datetime,
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
        created_cutoff: dt.datetime | None = None,
        forecast_horizon_minutes: int = 0,
        forecaster_name: str | None = None,
        forecaster_version: str | None = None,
    ) -> list[models.PredictedGenerationValue]:
        return []


def _make_app(db: models.StorageInterface, permissions: list[str]) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[models.get_storage_client] = lambda: db
    app.dependency_overrides[_auth_dep] = lambda: {
        "sub": "test|user",
        "permissions": permissions,
    }
    return app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with DummyDB backend and GB read access."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["read:uk"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with ocf:admin + GB read permissions."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["ocf:admin", "read:uk"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def no_perm_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with no permissions — used to assert 403 on data routes."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), [])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def intraday_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with GB intraday-only permission."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["read:uk-intraday"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def trial_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with trial permission (all countries)."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["read:trial"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def nl_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with NL read permission backed by NationNameStorageClient."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(NationNameStorageClient("nl_national"), ["read:nl"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def nl_trial_client() -> AsyncGenerator[AsyncClient, None]:
    """Trial client backed by NationNameStorageClient for NL cross-permission tests."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(NationNameStorageClient("nl_national"), ["read:trial"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def fixed_uuid_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client backed by FixedUUIDStorageClient for cache key tests."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(FixedUUIDStorageClient(), ["read:uk"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def null_uuid_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client that returns empty for location lookups with a UUID filter."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(NullLocationsForUUIDClient(), ["read:uk"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def nation_response_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client that returns NATION-type locations for untyped UUID lookups."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(NationResponseClient(), ["read:uk"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def empty_forecast_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client that returns no forecast data from get_predicted_generation."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(EmptyForecastClient(), ["read:uk"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def fixed_uuid_generation_client() -> AsyncGenerator[AsyncClient, None]:
    """FixedUUIDStorageClient-backed client for generation period window tests."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(FixedUUIDStorageClient(), ["read:uk"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _set_forecast_meta() -> None:
    """Inject a minimal forecast _meta key into the current cache backend."""
    now = dt.datetime.now(tz=dt.UTC)
    meta = {
        "model_name": "blend",
        "model_version": "1.0.0",
        "created_time": now.isoformat(),
        "init_time": now.isoformat(),
    }
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    await backend.set(
        f"{prefix}:v1:timeseries:GB:solar:gsp:_meta",
        json.dumps(meta).encode(),
        expire=3600,
    )


async def _set_fixed_region_values(values: list[dict]) -> None:
    """Inject per-region cache values for _FIXED_GSP_UUID into the current backend."""
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    await backend.set(
        f"{prefix}:v1:timeseries:GB:solar:gsp:{_FIXED_GSP_UUID}",
        json.dumps(values).encode(),
        expire=3600,
    )


async def _set_generation_meta(observer: str = "pvlive_in_day") -> None:
    """Inject a minimal generation _meta key into the current cache backend."""
    meta = {"observer_name": observer}
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    await backend.set(
        f"{prefix}:v1:timeseries:generation:GB:solar:gsp:{observer}:_meta",
        json.dumps(meta).encode(),
        expire=3600,
    )


async def _set_fixed_generation_values(
    values: list[dict],
    observer: str = "pvlive_in_day",
) -> None:
    """Inject per-region generation cache values for _FIXED_GSP_UUID."""
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    await backend.set(
        f"{prefix}:v1:timeseries:generation:GB:solar:gsp:{observer}:{_FIXED_GSP_UUID}",
        json.dumps(values).encode(),
        expire=3600,
    )


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
    resp = await client.get("/v1/GB/solar/region-types")
    assert resp.status_code == 200
    types = {rt["type"] for rt in resp.json()}
    assert "gsp" in types
    assert "national" in types


@pytest.mark.anyio
async def test_get_region_types_has_forecast_models(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/region-types")
    gsp = next(rt for rt in resp.json() if rt["type"] == "gsp")
    assert len(gsp["forecast_models"]) > 0
    model_names = [m["name"] for m in gsp["forecast_models"]]
    assert "blend" in model_names


@pytest.mark.anyio
async def test_get_region_types_has_default_model(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/region-types")
    assert resp.status_code == 200
    for rt in resp.json():
        assert "default_model" in rt
    national = next(rt for rt in resp.json() if rt["type"] == "national")
    assert national["default_model"] == "blend_adjust"


@pytest.mark.anyio
async def test_get_generation_sources(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/generation-sources")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert "pvlive_in_day" in names
    assert "pvlive_day_after" in names


@pytest.mark.anyio
async def test_unknown_country_returns_422(client: AsyncClient) -> None:
    resp = await client.get("/v1/XX/solar/region-types")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Region browsing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_countries(client: AsyncClient) -> None:
    resp = await client.get("/v1/countries")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_regions_by_type(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/regions?region_type=gsp")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) > 0


@pytest.mark.anyio
async def test_get_regions_by_parent_id(client: AsyncClient) -> None:
    parent_id = str(uuid4())
    resp = await client.get(f"/v1/GB/solar/regions?parent_id={parent_id}")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_regions_invalid_region_type_returns_400(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/regions?region_type=unknown_type")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_region_by_id(client: AsyncClient) -> None:
    region_id = str(uuid4())
    resp = await client.get(f"/v1/GB/solar/regions/{region_id}")
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
    resp = await client.get(f"/v1/GB/solar/regions/{region_id}/forecast")
    assert resp.status_code == 200
    body = resp.json()
    assert "capacity_kW" in body
    assert "values" in body
    assert "model_name" in body
    assert "init_time" in body


@pytest.mark.anyio
async def test_get_region_forecast_national_slug(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/regions/national/forecast")
    assert resp.status_code == 200
    body = resp.json()
    assert "capacity_kW" in body
    assert "values" in body


@pytest.mark.anyio
async def test_get_region_forecast_last_updated(client: AsyncClient) -> None:
    region_id = str(uuid4())
    resp = await client.get(f"/v1/GB/solar/regions/{region_id}/forecast/last-updated")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Per-region generation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_region_generation(client: AsyncClient) -> None:
    region_id = str(uuid4())
    resp = await client.get(f"/v1/GB/solar/regions/{region_id}/generation")
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
    resp = await client.get("/v1/GB/solar/forecasts/snapshot?region_type=gsp")
    assert resp.status_code == 200
    body = resp.json()
    assert "time" in body
    assert "values" in body


@pytest.mark.anyio
async def test_get_forecasts_snapshot_requires_region_type(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/forecasts/snapshot")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_generation_snapshot(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/generation/snapshot?region_type=gsp")
    assert resp.status_code == 200
    body = resp.json()
    assert "time" in body
    assert "observer_name" in body
    assert "values" in body


@pytest.mark.anyio
async def test_get_generation_snapshot_requires_region_type(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/generation/snapshot")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_forecasts_snapshot_national_fallback(client: AsyncClient) -> None:
    """National snapshot uses get_predicted_generation (not get_predicted_generation_snapshot)."""
    resp = await client.get("/v1/GB/solar/forecasts/snapshot?region_type=national")
    assert resp.status_code == 200
    body = resp.json()
    assert "time" in body
    assert "values" in body


@pytest.mark.anyio
async def test_get_forecasts_snapshot_invalid_region_type_returns_400(
    client: AsyncClient,
) -> None:
    resp = await client.get("/v1/GB/solar/forecasts/snapshot?region_type=unknown_type")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_forecasts_period_invalid_region_type_returns_400(
    client: AsyncClient,
) -> None:
    resp = await client.get("/v1/GB/solar/forecasts/period?region_type=unknown_type")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Period (matrix) endpoints — cold cache → 503
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_forecasts_period_cold_cache_returns_503(client: AsyncClient) -> None:
    """Period endpoint must 503 when cache has not been pre-warmed."""
    FastAPICache.init(InMemoryBackend(), prefix="cold")
    resp = await client.get("/v1/GB/solar/forecasts/period?region_type=gsp")
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers


@pytest.mark.anyio
async def test_get_generation_period_cold_cache_returns_503(client: AsyncClient) -> None:
    FastAPICache.init(InMemoryBackend(), prefix="cold2")
    resp = await client.get("/v1/GB/solar/generation/period?region_type=gsp")
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers


@pytest.mark.anyio
async def test_get_forecasts_period_warm_cache(client: AsyncClient) -> None:
    """Period endpoint returns RegionForecastMatrix when _meta is cached."""
    await _set_forecast_meta()
    resp = await client.get("/v1/GB/solar/forecasts/period?region_type=gsp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_name"] == "blend"
    assert "regions" in body


@pytest.mark.anyio
async def test_get_generation_period_warm_cache(client: AsyncClient) -> None:
    """Generation period endpoint returns RegionGenerationMatrix when _meta is cached."""
    observer = "pvlive_in_day"
    meta = {"observer_name": observer}
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    await backend.set(
        f"{prefix}:v1:timeseries:generation:GB:solar:gsp:{observer}:_meta",
        json.dumps(meta).encode(),
        expire=3600,
    )

    resp = await client.get(
        f"/v1/GB/solar/generation/period?region_type=gsp&observer={observer}",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["observer_name"] == observer
    assert "regions" in body


@pytest.mark.anyio
async def test_get_forecasts_period_region_ids_filter(client: AsyncClient) -> None:
    """region_ids filter reduces result to matching regions only."""
    await _set_forecast_meta()
    non_existent_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/forecasts/period?region_type=gsp&region_ids={non_existent_id}",
    )
    assert resp.status_code == 200
    assert resp.json()["regions"] == []


@pytest.mark.anyio
async def test_get_forecasts_period_window_includes_cached_values(
    fixed_uuid_client: AsyncClient,
) -> None:
    """Values within the requested window are returned; values outside are excluded."""
    now = dt.datetime.now(tz=dt.UTC).replace(microsecond=0)
    t_early = now - dt.timedelta(hours=3)
    t_late = now + dt.timedelta(hours=3)

    await _set_forecast_meta()
    await _set_fixed_region_values([
        {"time": t_early.isoformat(), "power_kW": 100.0, "plevels_kW": {}},
        {"time": t_late.isoformat(), "power_kW": 200.0, "plevels_kW": {}},
    ])

    # Narrow window: only t_early should be included
    resp = await fixed_uuid_client.get(
        "/v1/GB/solar/forecasts/period",
        params={
            "region_type": "gsp",
            "start_utc": (t_early - dt.timedelta(minutes=1)).isoformat(),
            "end_utc": (t_early + dt.timedelta(minutes=1)).isoformat(),
        },
    )
    assert resp.status_code == 200
    regions = resp.json()["regions"]
    assert len(regions) == 1
    assert len(regions[0]["values"]) == 1
    assert regions[0]["values"][0]["power_kW"] == 100.0


@pytest.mark.anyio
async def test_get_forecasts_period_window_excludes_out_of_range_values(
    fixed_uuid_client: AsyncClient,
) -> None:
    """Values outside the requested window are not returned."""
    now = dt.datetime.now(tz=dt.UTC).replace(microsecond=0)
    t_past = now - dt.timedelta(hours=10)

    await _set_forecast_meta()
    await _set_fixed_region_values([
        {"time": t_past.isoformat(), "power_kW": 50.0, "plevels_kW": {}},
    ])

    # Window starts after the cached value — should be empty
    resp = await fixed_uuid_client.get(
        "/v1/GB/solar/forecasts/period",
        params={
            "region_type": "gsp",
            "start_utc": (now - dt.timedelta(hours=1)).isoformat(),
            "end_utc": (now + dt.timedelta(hours=1)).isoformat(),
        },
    )
    assert resp.status_code == 200
    regions = resp.json()["regions"]
    assert len(regions) == 1
    assert regions[0]["values"] == []


# ---------------------------------------------------------------------------
# Admin refresh endpoints
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_refresh_forecasts_requires_admin(client: AsyncClient) -> None:
    resp = await client.post("/v1/GB/solar/forecasts/refresh?region_type=gsp")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_refresh_forecasts_as_admin(admin_client: AsyncClient) -> None:
    resp = await admin_client.post("/v1/GB/solar/forecasts/refresh?region_type=gsp")
    assert resp.status_code == 202


@pytest.mark.anyio
async def test_refresh_generation_requires_admin(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/GB/solar/generation/refresh?region_type=gsp&observer=pvlive_in_day",
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_refresh_generation_as_admin(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        "/v1/GB/solar/generation/refresh?region_type=gsp&observer=pvlive_in_day",
    )
    assert resp.status_code == 202


# ---------------------------------------------------------------------------
# Region browsing — branch logic
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_regions_no_filter_returns_all(client: AsyncClient) -> None:
    """No filters: response includes nation plus child region types."""
    resp = await client.get("/v1/GB/solar/regions")
    assert resp.status_code == 200
    regions = resp.json()
    assert isinstance(regions, list)
    assert len(regions) >= 1


@pytest.mark.anyio
async def test_get_regions_national_type_returns_nation(client: AsyncClient) -> None:
    """region_type=national returns only the nation region."""
    resp = await client.get("/v1/GB/solar/regions?region_type=national")
    assert resp.status_code == 200
    regions = resp.json()
    assert len(regions) == 1
    assert regions[0]["type"] == "national"


@pytest.mark.anyio
async def test_get_regions_parent_id_returns_children(client: AsyncClient) -> None:
    """parent_id with any UUID returns child locations (DummyDB always responds)."""
    parent_id = str(uuid4())
    resp = await client.get(f"/v1/GB/solar/regions?parent_id={parent_id}")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_regions_parent_id_with_region_type(client: AsyncClient) -> None:
    """parent_id + region_type=gsp returns GSP children of that parent."""
    parent_id = str(uuid4())
    resp = await client.get(f"/v1/GB/solar/regions?parent_id={parent_id}&region_type=gsp")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_regions_parent_not_in_country_404(null_uuid_client: AsyncClient) -> None:
    """parent_id not found within the country boundary returns 404."""
    parent_id = str(uuid4())
    resp = await null_uuid_client.get(f"/v1/GB/solar/regions?parent_id={parent_id}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_regions_name_filter_returns_match(client: AsyncClient) -> None:
    """?name= returns only regions whose name contains the substring (case-insensitive)."""
    resp = await client.get("/v1/GB/solar/regions?name=dummy+gsp")
    assert resp.status_code == 200
    regions = resp.json()
    assert all("dummy gsp" in r["name"].lower() for r in regions)


@pytest.mark.anyio
async def test_get_regions_name_filter_no_match_returns_empty(client: AsyncClient) -> None:
    """?name= with no matching regions returns an empty list."""
    resp = await client.get("/v1/GB/solar/regions?name=zzznomatch")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_get_regions_name_filter_is_case_insensitive(client: AsyncClient) -> None:
    """?name= match is case-insensitive."""
    resp_lower = await client.get("/v1/GB/solar/regions?name=dummy+gsp")
    resp_upper = await client.get("/v1/GB/solar/regions?name=DUMMY+GSP")
    assert resp_lower.status_code == 200
    assert resp_upper.status_code == 200
    names_lower = [r["name"] for r in resp_lower.json()]
    names_upper = [r["name"] for r in resp_upper.json()]
    assert names_lower == names_upper


@pytest.mark.anyio
async def test_get_regions_region_type_wrong_country_400(client: AsyncClient) -> None:
    """Region type valid for NL (provinces) is unknown for GB → 400."""
    resp = await client.get("/v1/GB/solar/regions?region_type=provinces")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Per-region forecast — parameter combinations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_region_forecast_beyond_one_year_400(client: AsyncClient) -> None:
    """start_utc older than 1 rolling year returns 400 with an extended-access message."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast",
        params={"start_utc": (dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=400)).isoformat()},
    )
    assert resp.status_code == 400
    assert "1-year" in resp.json()["detail"]


@pytest.mark.anyio
async def test_get_region_generation_beyond_one_year_400(client: AsyncClient) -> None:
    """start_utc older than 1 rolling year on generation endpoint returns 400."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/generation",
        params={"start_utc": (dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=400)).isoformat()},
    )
    assert resp.status_code == 400
    assert "1-year" in resp.json()["detail"]


@pytest.mark.anyio
async def test_get_region_forecast_invalid_model_for_region_type_400(
    client: AsyncClient,
) -> None:
    """Day-ahead model not available for GSP returns 400.

    DummyDB returns a GSP location for untyped lookups; pvnet_day_ahead is not
    in the GSP model list (only blend + intraday models are valid for GSP).
    """
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model=pvnet_day_ahead",
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_region_forecast_blend_adjust_on_national_200(
    nation_response_client: AsyncClient,
) -> None:
    """blend_adjust is a valid model for national region type — returns 200."""
    region_id = str(uuid4())
    resp = await nation_response_client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model=blend_adjust",
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_region_forecast_creation_limit_utc(client: AsyncClient) -> None:
    """creation_limit_utc passes through to backend — endpoint returns 200."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast",
        params={"creation_limit_utc": dt.datetime.now(tz=dt.UTC).isoformat()},
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_region_forecast_horizon_minutes(client: AsyncClient) -> None:
    """forecast_horizon_minutes passes through to backend — endpoint returns 200."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?forecast_horizon_minutes=1440",
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_region_forecast_start_and_end_utc(client: AsyncClient) -> None:
    """Explicit start_utc + end_utc window is accepted — endpoint returns 200."""
    region_id = str(uuid4())
    now = dt.datetime.now(tz=dt.UTC)
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast",
        params={
            "start_utc": (now - dt.timedelta(days=1)).isoformat(),
            "end_utc": now.isoformat(),
        },
    )
    assert resp.status_code == 200
    assert "values" in resp.json()


@pytest.mark.anyio
async def test_get_region_forecast_invalid_region_id_format_422(
    client: AsyncClient,
) -> None:
    """Non-UUID, non-'national' region_id string returns 422."""
    resp = await client.get("/v1/GB/solar/regions/NATIONAL/forecast")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Per-region forecast — last-updated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_region_forecast_last_updated_explicit_model(
    client: AsyncClient,
) -> None:
    """Explicit model param is accepted; last-updated returns 200 when forecasts exist."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast/last-updated?model=blend",
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_region_forecast_last_updated_no_recent_forecasts_404(
    empty_forecast_client: AsyncClient,
) -> None:
    """last-updated returns 404 when no forecasts exist in the ±30-min window."""
    region_id = str(uuid4())
    resp = await empty_forecast_client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast/last-updated",
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Per-region generation — parameter combinations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_region_generation_day_after_observer(client: AsyncClient) -> None:
    """observer=pvlive_day_after is a valid alternative observer — returns 200."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/generation?observer=pvlive_day_after",
    )
    assert resp.status_code == 200
    assert resp.json()["observer_name"] == "pvlive_day_after"


@pytest.mark.anyio
async def test_get_region_generation_time_window(client: AsyncClient) -> None:
    """Explicit start_utc + end_utc window is forwarded — endpoint returns 200."""
    region_id = str(uuid4())
    now = dt.datetime.now(tz=dt.UTC)
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/generation",
        params={
            "start_utc": (now - dt.timedelta(days=3)).isoformat(),
            "end_utc": now.isoformat(),
        },
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_region_generation_invalid_observer_422(client: AsyncClient) -> None:
    """observer not matching the allowed pattern returns 422."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/generation?observer=not_a_real_observer",
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Per-region — 404 on missing region
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_region_by_id_not_found_404(null_uuid_client: AsyncClient) -> None:
    """Region lookup returning empty list from DP yields 404."""
    region_id = str(uuid4())
    resp = await null_uuid_client.get(f"/v1/GB/solar/regions/{region_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Forecast snapshot — parameter combinations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_forecasts_snapshot_explicit_timestamp(client: AsyncClient) -> None:
    """Explicit timestamp is used instead of floored-now — returns 200."""
    ts = "2026-04-17T12:00:00Z"
    resp = await client.get(
        f"/v1/GB/solar/forecasts/snapshot?region_type=gsp&timestamp={ts}",
    )
    assert resp.status_code == 200
    assert resp.json()["time"].startswith("2026-04-17T12:00:00")


@pytest.mark.anyio
async def test_get_forecasts_snapshot_invalid_model_name_400(
    client: AsyncClient,
) -> None:
    """model_name not in the GSP model list returns 400."""
    resp = await client.get(
        "/v1/GB/solar/forecasts/snapshot?region_type=gsp&model_name=not_a_model",
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_forecasts_snapshot_model_version_passthrough(
    client: AsyncClient,
) -> None:
    """model_version is forwarded to the backend — endpoint returns 200."""
    resp = await client.get(
        "/v1/GB/solar/forecasts/snapshot?region_type=gsp&model_version=1.2.3",
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_forecasts_snapshot_model_name_and_version(
    client: AsyncClient,
) -> None:
    """model_name + model_version together are accepted — returns 200."""
    resp = await client.get(
        "/v1/GB/solar/forecasts/snapshot?region_type=gsp&model_name=blend&model_version=1.0.0",
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_forecasts_snapshot_naive_timestamp_gets_utc(
    client: AsyncClient,
) -> None:
    """Naive (tz-unaware) timestamp is accepted and treated as UTC — returns 200."""
    resp = await client.get(
        "/v1/GB/solar/forecasts/snapshot?region_type=gsp&timestamp=2026-04-17T12:00:00",
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Generation snapshot — parameter combinations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_generation_snapshot_day_after_observer(client: AsyncClient) -> None:
    """observer=pvlive_day_after is passed to the backend — returns 200."""
    resp = await client.get(
        "/v1/GB/solar/generation/snapshot?region_type=gsp&observer=pvlive_day_after",
    )
    assert resp.status_code == 200
    assert resp.json()["observer_name"] == "pvlive_day_after"


@pytest.mark.anyio
async def test_get_generation_snapshot_explicit_timestamp(client: AsyncClient) -> None:
    """Explicit timestamp is used for the generation snapshot — returns 200."""
    ts = "2026-04-17T10:30:00Z"
    resp = await client.get(
        f"/v1/GB/solar/generation/snapshot?region_type=gsp&timestamp={ts}",
    )
    assert resp.status_code == 200
    assert resp.json()["time"].startswith("2026-04-17T10:30:00")


@pytest.mark.anyio
async def test_get_generation_snapshot_invalid_observer_422(
    client: AsyncClient,
) -> None:
    """observer not matching the allowed pattern returns 422."""
    resp = await client.get(
        "/v1/GB/solar/generation/snapshot?region_type=gsp&observer=bogus",
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_generation_snapshot_naive_timestamp_gets_utc(
    client: AsyncClient,
) -> None:
    """Naive timestamp for generation snapshot is treated as UTC — returns 200."""
    resp = await client.get(
        "/v1/GB/solar/generation/snapshot?region_type=gsp&timestamp=2026-04-17T10:30:00",
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Forecast period — parameter combinations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_forecasts_period_start_and_end_utc(client: AsyncClient) -> None:
    """Explicit start_utc + end_utc sub-queries the in-memory cache window."""
    await _set_forecast_meta()
    now = dt.datetime.now(tz=dt.UTC)
    resp = await client.get(
        "/v1/GB/solar/forecasts/period",
        params={
            "region_type": "gsp",
            "start_utc": (now - dt.timedelta(hours=1)).isoformat(),
            "end_utc": now.isoformat(),
        },
    )
    assert resp.status_code == 200
    assert "regions" in resp.json()


@pytest.mark.anyio
async def test_get_forecasts_period_multiple_region_ids_no_match(
    client: AsyncClient,
) -> None:
    """region_ids with UUIDs not present in cache yields empty regions list."""
    await _set_forecast_meta()
    id1, id2 = str(uuid4()), str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/forecasts/period?region_type=gsp&region_ids={id1}&region_ids={id2}",
    )
    assert resp.status_code == 200
    assert resp.json()["regions"] == []


# ---------------------------------------------------------------------------
# Generation period — parameter combinations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_generation_period_day_after_observer_503(
    client: AsyncClient,
) -> None:
    """pvlive_day_after uses a separate cache key — 503 when that key is cold."""
    FastAPICache.init(InMemoryBackend(), prefix="cold_gen")
    resp = await client.get(
        "/v1/GB/solar/generation/period?region_type=gsp&observer=pvlive_day_after",
    )
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers


@pytest.mark.anyio
async def test_get_generation_period_warm_day_after(client: AsyncClient) -> None:
    """pvlive_day_after generation period endpoint returns 200 when its cache is warm."""
    await _set_generation_meta(observer="pvlive_day_after")
    resp = await client.get(
        "/v1/GB/solar/generation/period?region_type=gsp&observer=pvlive_day_after",
    )
    assert resp.status_code == 200
    assert resp.json()["observer_name"] == "pvlive_day_after"


@pytest.mark.anyio
async def test_get_generation_period_start_and_end_utc(
    fixed_uuid_generation_client: AsyncClient,
) -> None:
    """Explicit window sub-queries the generation cache; values outside are excluded."""
    now = dt.datetime.now(tz=dt.UTC).replace(microsecond=0)
    t_early = now - dt.timedelta(hours=3)
    t_late = now + dt.timedelta(hours=3)

    await _set_generation_meta()
    await _set_fixed_generation_values([
        {"time": t_early.isoformat(), "power_kW": 111.0},
        {"time": t_late.isoformat(), "power_kW": 222.0},
    ])

    resp = await fixed_uuid_generation_client.get(
        "/v1/GB/solar/generation/period",
        params={
            "region_type": "gsp",
            "start_utc": (t_early - dt.timedelta(minutes=1)).isoformat(),
            "end_utc": (t_early + dt.timedelta(minutes=1)).isoformat(),
        },
    )
    assert resp.status_code == 200
    regions = resp.json()["regions"]
    assert len(regions) == 1
    assert len(regions[0]["values"]) == 1
    assert regions[0]["values"][0]["power_kW"] == 111.0


@pytest.mark.anyio
async def test_get_generation_period_region_ids_filter(client: AsyncClient) -> None:
    """region_ids with non-matching UUIDs returns empty regions list."""
    await _set_generation_meta()
    non_existent = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/generation/period?region_type=gsp&region_ids={non_existent}",
    )
    assert resp.status_code == 200
    assert resp.json()["regions"] == []


# ---------------------------------------------------------------------------
# Admin refresh — non-default parameters
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_refresh_forecasts_non_default_region_type(
    admin_client: AsyncClient,
) -> None:
    """refresh endpoint accepts non-default region_type (dno) — returns 202."""
    resp = await admin_client.post("/v1/GB/solar/forecasts/refresh?region_type=dno")
    assert resp.status_code == 202


@pytest.mark.anyio
async def test_refresh_generation_day_after_observer(admin_client: AsyncClient) -> None:
    """refresh endpoint with pvlive_day_after observer triggers separate warm — 202."""
    resp = await admin_client.post(
        "/v1/GB/solar/generation/refresh?region_type=gsp&observer=pvlive_day_after",
    )
    assert resp.status_code == 202


# ---------------------------------------------------------------------------
# Edge values / Tier 2
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_region_id_uppercase_national_422(client: AsyncClient) -> None:
    """'NATIONAL' (wrong case) is not a valid UUID and not the 'national' slug → 422."""
    resp = await client.get("/v1/GB/solar/regions/NATIONAL/generation")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_country_lowercase_422(client: AsyncClient) -> None:
    """Country code in lowercase is not a valid CountryCode enum value → 422."""
    resp = await client.get("/v1/gb/solar/region-types")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_forecasts_snapshot_invalid_region_type_for_country_400(
    client: AsyncClient,
) -> None:
    """provinces is unknown for GB → 400 (valid for NL but not GB)."""
    resp = await client.get(
        "/v1/GB/solar/forecasts/snapshot?region_type=provinces",
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_generation_snapshot_invalid_region_type_for_country_400(
    client: AsyncClient,
) -> None:
    """provinces is unknown for GB → 400 on generation snapshot too."""
    resp = await client.get(
        "/v1/GB/solar/generation/snapshot?region_type=provinces",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Auth — country-level access control
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_data_route_no_permissions_returns_403(no_perm_client: AsyncClient) -> None:
    """No permissions → 403 on any country-scoped data route."""
    resp = await no_perm_client.get("/v1/GB/solar/regions?region_type=gsp")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_data_route_wrong_country_returns_403() -> None:
    """NL permission only → 403 on GB data route."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["read:nl"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/v1/GB/solar/regions?region_type=gsp")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_data_route_trial_permission_grants_access(trial_client: AsyncClient) -> None:
    """read:trial bypasses country check → 200 on GB data route."""
    resp = await trial_client.get("/v1/GB/solar/regions?region_type=gsp")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_data_route_partner_permission_grants_access() -> None:
    """read:partner bypasses country check → 200 on GB data route."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["read:partner"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/v1/GB/solar/regions?region_type=gsp")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_discovery_routes_are_public(no_perm_client: AsyncClient) -> None:
    """Discovery endpoints (sources, region-types, countries) require no auth."""
    for url in [
        "/v1/sources",
        "/v1/countries",
        "/v1/GB/solar/region-types",
        "/v1/GB/solar/generation-sources",
    ]:
        resp = await no_perm_client.get(url)
        assert resp.status_code == 200, f"Expected 200 on {url}, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Auth — intraday model restriction
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_intraday_user_forecast_defaults_to_intraday_model(
    intraday_client: AsyncClient,
) -> None:
    """Intraday-only user gets pvnet_intra_allbells0 as the default model."""
    region_id = str(uuid4())
    resp = await intraday_client.get(f"/v1/GB/solar/regions/{region_id}/forecast")
    assert resp.status_code == 200
    # DummyDB echoes back the forecaster_name; intraday default should be used.
    assert resp.json()["model_name"] == "pvnet_intra_allbells0"


@pytest.mark.anyio
async def test_intraday_user_requesting_intraday_model_200(
    intraday_client: AsyncClient,
) -> None:
    """Intraday-only user can explicitly request an intraday model → 200."""
    region_id = str(uuid4())
    resp = await intraday_client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model=pvnet_intra_allbells0",
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_intraday_user_requesting_non_intraday_model_403(
    intraday_client: AsyncClient,
) -> None:
    """Intraday-only user requesting blend (a day-ahead model) → 403."""
    region_id = str(uuid4())
    resp = await intraday_client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model=blend",
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_intraday_user_can_access_generation_data(
    intraday_client: AsyncClient,
) -> None:
    """Intraday users have country access and can read observed generation data."""
    region_id = str(uuid4())
    resp = await intraday_client.get(f"/v1/GB/solar/regions/{region_id}/generation")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# NL country — discovery, access control, and data routes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_nl_region_types_are_accessible(nl_client: AsyncClient) -> None:
    """NL region-types discovery is public and returns national + provinces."""
    resp = await nl_client.get("/v1/NL/solar/region-types")
    assert resp.status_code == 200
    types = {rt["type"] for rt in resp.json()}
    assert "national" in types
    assert "provinces" in types
    assert "gsp" not in types


@pytest.mark.anyio
async def test_nl_region_types_public_no_auth(no_perm_client: AsyncClient) -> None:
    """NL region-types is public — no permission required."""
    resp = await no_perm_client.get("/v1/NL/solar/region-types")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_nl_regions_requires_nl_permission(client: AsyncClient) -> None:
    """read:uk only → 403 on NL data routes."""
    resp = await client.get("/v1/NL/solar/regions?region_type=national")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_nl_regions_accessible_with_read_nl(nl_client: AsyncClient) -> None:
    """read:nl grants access to NL region listing."""
    resp = await nl_client.get("/v1/NL/solar/regions?region_type=national")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_nl_regions_provinces_type(nl_client: AsyncClient) -> None:
    """provinces is a valid region type for NL → 200."""
    resp = await nl_client.get("/v1/NL/solar/regions?region_type=provinces")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_nl_regions_invalid_type_gsp_400(nl_client: AsyncClient) -> None:
    """gsp is not a valid region type for NL → 400."""
    resp = await nl_client.get("/v1/NL/solar/regions?region_type=gsp")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_nl_forecast_default_model(nl_client: AsyncClient) -> None:
    """NL national forecast returns the NL default model."""
    resp = await nl_client.get("/v1/NL/solar/regions/national/forecast")
    assert resp.status_code == 200
    assert resp.json()["model_name"] == "nl_regional_pv_ecmwf_mo_sat_adjust"


@pytest.mark.anyio
async def test_nl_forecast_invalid_model_400(nl_client: AsyncClient) -> None:
    """blend is not a valid NL model → 400."""
    resp = await nl_client.get("/v1/NL/solar/regions/national/forecast?model=blend")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_nl_generation_sources(nl_client: AsyncClient) -> None:
    """NL generation sources discovery is public and includes nednl."""
    resp = await nl_client.get("/v1/NL/solar/generation-sources")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert "nednl" in names


@pytest.mark.anyio
async def test_nl_accessible_with_trial_permission(nl_trial_client: AsyncClient) -> None:
    """read:trial grants access to NL data routes."""
    resp = await nl_trial_client.get("/v1/NL/solar/regions?region_type=national")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_nl_forecast_snapshot(nl_client: AsyncClient) -> None:
    """NL forecast snapshot for national region type returns 200."""
    resp = await nl_client.get("/v1/NL/solar/forecasts/snapshot?region_type=national")
    assert resp.status_code == 200
    assert "time" in resp.json()
    assert "values" in resp.json()
