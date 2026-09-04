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

from quartz_api.internal import eclipse, models
from quartz_api.internal.backends.dummydb.client import StorageClient
from quartz_api.internal.middleware.auth import AuthDependency

from .country_config import COUNTRIES, FM, RegionTypeConfig
from .helpers import resolve_forecast_model
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
            authdata={},
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
            authdata={},
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
            authdata={},
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
            authdata={},
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
    app = _make_app(StorageClient(), ["read:gb"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with ocf:admin + GB read permissions."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["ocf:admin", "read:gb"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def no_perm_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with no permissions — used to assert 403 on data routes."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), [])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def intraday_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with GB intraday-only permission."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["read:uk-intraday"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def trial_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with trial permission (all countries)."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["read:trial"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


_FIXED_NL_PROVINCE_UUID = uuid4()
_NL_PROVINCE_INTERNAL_NAME = "nl_region_2_friesland"
_NL_PROVINCE_DISPLAY_NAME = "friesland"


class NLProvinceClient(NationNameStorageClient):
    """NL client that returns a stable UUID for the Friesland province.

    Allows cache-key tests that verify display-name filtering works for NL.
    """

    def __init__(self) -> None:
        super().__init__("nl_national")

    async def get_locations(  # type: ignore[override]
        self,
        energy_type: models.EnergyType,
        location_type: models.LocationType | None,
        authdata: dict,
        location_uuid: UUID | None = None,
        enclosing_location_uuid: UUID | None = None,
    ) -> list[models.Location]:
        if location_type == models.LocationType.REGION:
            return [
                models.Location(
                    uuid=_FIXED_NL_PROVINCE_UUID,
                    name=_NL_PROVINCE_INTERNAL_NAME,
                    latitude=53.0,
                    longitude=5.8,
                    capacity_kilowatts=500_000,
                    location_type=models.LocationType.REGION,
                ),
            ]
        return await super().get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata={},
            location_uuid=location_uuid,
            enclosing_location_uuid=enclosing_location_uuid,
        )


@pytest_asyncio.fixture
async def nl_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with NL read permission backed by NationNameStorageClient."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(NationNameStorageClient("nl_national"), ["read:nl"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def nl_trial_client() -> AsyncGenerator[AsyncClient, None]:
    """Trial client backed by NationNameStorageClient for NL cross-permission tests."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(NationNameStorageClient("nl_national"), ["read:trial"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def fixed_uuid_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client backed by FixedUUIDStorageClient for cache key tests."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(FixedUUIDStorageClient(), ["read:gb"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def null_uuid_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client that returns empty for location lookups with a UUID filter."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(NullLocationsForUUIDClient(), ["read:gb"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def nation_response_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client that returns NATION-type locations for untyped UUID lookups."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(NationResponseClient(), ["read:gb"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def empty_forecast_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client that returns no forecast data from get_predicted_generation."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(EmptyForecastClient(), ["read:gb"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def fixed_uuid_generation_client() -> AsyncGenerator[AsyncClient, None]:
    """FixedUUIDStorageClient-backed client for generation period window tests."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(FixedUUIDStorageClient(), ["read:gb"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


async def _set_forecast_meta() -> None:
    """Inject a minimal forecast _meta key into the current cache backend."""
    now = dt.datetime.now(tz=dt.UTC)
    meta = {
        "model_name": "blend",
        "model_version": "1.0.0",
        "last_updated_utc": now.isoformat(),
        "latest_init_utc": now.isoformat(),
    }
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    await backend.set(
        f"{prefix}:v1:period:GB:solar:gsp:_meta",
        json.dumps(meta).encode(),
        expire=3600,
    )


async def _set_fixed_region_values(values: list[dict]) -> None:
    """Inject per-region cache values for _FIXED_GSP_UUID into the current backend."""
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    await backend.set(
        f"{prefix}:v1:period:GB:solar:gsp:{_FIXED_GSP_UUID}",
        json.dumps(values).encode(),
        expire=3600,
    )


async def _set_generation_meta(observer: str = "pvlive_in_day") -> None:
    """Inject a minimal generation _meta key into the current cache backend."""
    meta = {"observer_name": observer}
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    await backend.set(
        f"{prefix}:v1:period:generation:GB:solar:gsp:{observer}:_meta",
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
        f"{prefix}:v1:period:generation:GB:solar:gsp:{observer}:{_FIXED_GSP_UUID}",
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
    # The adjusted variant is reached via ?adjusted, not via the model name.
    assert national["default_model"] == "blend"


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
    resp = await client.get(f"/v1/GB/solar/regions?parent={parent_id}")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_regions_invalid_region_type_returns_400(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/regions?region_type=unknown_type")
    assert resp.status_code == 400


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/v1/GB/solar/regions/x",
        "/v1/GB/solar/regions/x/forecast",
        "/v1/GB/solar/regions/x/generation",
    ],
)
async def test_single_char_region_returns_422(client: AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_region_by_id(client: AsyncClient) -> None:
    region_id = str(uuid4())
    resp = await client.get(f"/v1/GB/solar/regions/{region_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "name" in body
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
    assert "latest_init_utc" in body


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/v1/GB/solar/regions/national/forecast?start_utc=2026-01-02T00:00:00Z&end_utc=2026-01-01T00:00:00Z",
        "/v1/GB/solar/regions/national/generation?start_utc=2026-01-02T00:00:00Z&end_utc=2026-01-01T00:00:00Z",
        "/v1/GB/solar/forecasts/period?region_type=gsp&start_utc=2026-01-02T00:00:00Z&end_utc=2026-01-01T00:00:00Z",
        "/v1/GB/solar/generation/period?region_type=gsp&start_utc=2026-01-02T00:00:00Z&end_utc=2026-01-01T00:00:00Z",
    ],
)
async def test_start_after_end_returns_400(client: AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.status_code == 400
    assert "start_utc" in resp.json()["detail"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/v1/GB/solar/regions/national/forecast?start_utc=2025-12-01T00:00:00Z&end_utc=2026-05-01T00:00:00Z",
        "/v1/GB/solar/regions/national/generation?start_utc=2025-12-01T00:00:00Z&end_utc=2026-05-01T00:00:00Z",
        "/v1/GB/solar/forecasts/period?region_type=gsp&start_utc=2025-12-01T00:00:00Z&end_utc=2026-05-01T00:00:00Z",
        "/v1/GB/solar/generation/period?region_type=gsp&start_utc=2025-12-01T00:00:00Z&end_utc=2026-05-01T00:00:00Z",
    ],
)
async def test_window_over_3_months_returns_400(client: AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.status_code == 400
    assert "3-month" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Snapshot endpoints
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_forecasts_snapshot(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/forecasts/snapshot?region_type=gsp")
    assert resp.status_code == 200
    body = resp.json()
    assert "time_utc" in body
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
    assert "time_utc" in body
    assert "observer_name" in body
    assert "values" in body


@pytest.mark.anyio
async def test_get_generation_snapshot_requires_region_type(
    client: AsyncClient,
) -> None:
    resp = await client.get("/v1/GB/solar/generation/snapshot")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_forecasts_snapshot_national_fallback(client: AsyncClient) -> None:
    """National snapshot uses get_predicted_generation (not get_predicted_generation_snapshot)."""
    resp = await client.get("/v1/GB/solar/forecasts/snapshot?region_type=national")
    assert resp.status_code == 200
    body = resp.json()
    assert "time_utc" in body
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


@pytest.mark.anyio
async def test_get_forecasts_period_national_returns_400(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/forecasts/period?region_type=national")
    assert resp.status_code == 400
    assert "period endpoint" in resp.json()["detail"]


@pytest.mark.anyio
async def test_get_generation_period_national_returns_400(client: AsyncClient) -> None:
    resp = await client.get("/v1/GB/solar/generation/period?region_type=national")
    assert resp.status_code == 400
    assert "period endpoint" in resp.json()["detail"]


@pytest.mark.anyio
async def test_get_generation_period_invalid_observer_returns_400(
    client: AsyncClient,
) -> None:
    resp = await client.get(
        "/v1/GB/solar/generation/period?region_type=gsp&observer=unknown_obs",
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_generation_period_wrong_country_observer_returns_400(
    nl_client: AsyncClient,
) -> None:
    resp = await nl_client.get(
        "/v1/NL/solar/generation/period?region_type=province&observer=pvlive_in_day",
    )
    assert resp.status_code == 400
    assert "ned_nl" in resp.json()["detail"]


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
async def test_get_generation_period_cold_cache_returns_503(
    client: AsyncClient,
) -> None:
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
        f"{prefix}:v1:period:generation:GB:solar:gsp:{observer}:_meta",
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
    await _set_fixed_region_values(
        [
            {"time_utc": t_early.isoformat(), "power_kW": 100.0, "plevels_kW": {}},
            {"time_utc": t_late.isoformat(), "power_kW": 200.0, "plevels_kW": {}},
        ],
    )

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
    body = resp.json()
    regions = body["regions"]
    assert len(regions) == 1
    assert len(body["times_utc"]) == 1
    assert regions[0]["power_kW"] == [100.0]


@pytest.mark.anyio
async def test_get_forecasts_period_window_excludes_out_of_range_values(
    fixed_uuid_client: AsyncClient,
) -> None:
    """Values outside the requested window are not returned."""
    now = dt.datetime.now(tz=dt.UTC).replace(microsecond=0)
    t_past = now - dt.timedelta(hours=10)

    await _set_forecast_meta()
    await _set_fixed_region_values(
        [
            {"time_utc": t_past.isoformat(), "power_kW": 50.0, "plevels_kW": {}},
        ],
    )

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
    body = resp.json()
    assert body["times_utc"] == []
    assert body["regions"][0]["power_kW"] == []


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
    resp = await client.get(f"/v1/GB/solar/regions?parent={parent_id}")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_regions_parent_id_with_region_type(client: AsyncClient) -> None:
    """parent_id + region_type=gsp returns GSP children of that parent."""
    parent_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions?parent={parent_id}&region_type=gsp",
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_regions_parent_not_in_country_404(
    null_uuid_client: AsyncClient,
) -> None:
    """parent_id not found within the country boundary returns 404."""
    parent_id = str(uuid4())
    resp = await null_uuid_client.get(f"/v1/GB/solar/regions?parent={parent_id}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_regions_name_filter_returns_match(client: AsyncClient) -> None:
    """?name= returns only regions whose name contains the substring (case-insensitive)."""
    resp = await client.get("/v1/GB/solar/regions?name=dummy+gsp")
    assert resp.status_code == 200
    regions = resp.json()
    assert all("dummy gsp" in r["name"].lower() for r in regions)


@pytest.mark.anyio
async def test_get_regions_name_filter_no_match_returns_empty(
    client: AsyncClient,
) -> None:
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
    """Region type valid for NL (province) is unknown for GB → 400."""
    resp = await client.get("/v1/GB/solar/regions?region_type=province")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Per-region forecast — parameter combinations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_region_forecast_beyond_one_year_422(client: AsyncClient) -> None:
    """start_utc older than 1 rolling year returns 422 with an extended-access message."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast",
        params={
            "start_utc": (
                dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=400)
            ).isoformat(),
        },
    )
    assert resp.status_code == 422
    assert "1-year" in resp.json()["detail"][0]["msg"]


@pytest.mark.anyio
async def test_get_region_generation_beyond_one_year_422(client: AsyncClient) -> None:
    """start_utc older than 1 rolling year on generation endpoint returns 422."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/generation",
        params={
            "start_utc": (
                dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=400)
            ).isoformat(),
        },
    )
    assert resp.status_code == 422
    assert "1-year" in resp.json()["detail"][0]["msg"]


@pytest.mark.anyio
async def test_get_region_forecast_invalid_model_for_region_type_400(
    client: AsyncClient,
) -> None:
    """Model not in the GSP model list returns 400.

    DummyDB returns a GSP location for untyped lookups; pvnet_ecmwf is a
    national-only model not available for GSP.
    """
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model=pvnet_ecmwf",
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
async def test_get_region_forecast_horizon_minutes_param(client: AsyncClient) -> None:
    """horizon_minutes passes through to backend — endpoint returns 200."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?horizon_minutes=1440",
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
async def test_get_region_forecast_name_not_found_404(
    client: AsyncClient,
) -> None:
    """Non-UUID, non-'national' region_id string is treated as a name search; unmatched → 404."""
    resp = await client.get("/v1/GB/solar/regions/NATIONAL/forecast")
    assert resp.status_code == 404


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
        f"/v1/GB/solar/forecasts/snapshot?region_type=gsp&time_utc={ts}",
    )
    assert resp.status_code == 200
    assert resp.json()["time_utc"].startswith("2026-04-17T12:00:00")


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
        "/v1/GB/solar/forecasts/snapshot?region_type=gsp&time_utc=2026-04-17T12:00:00",
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
        f"/v1/GB/solar/generation/snapshot?region_type=gsp&time_utc={ts}",
    )
    assert resp.status_code == 200
    assert resp.json()["time_utc"].startswith("2026-04-17T10:30:00")


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
        "/v1/GB/solar/generation/snapshot?region_type=gsp&time_utc=2026-04-17T10:30:00",
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
    await _set_fixed_generation_values(
        [
            {"time_utc": t_early.isoformat(), "power_kW": 111.0},
            {"time_utc": t_late.isoformat(), "power_kW": 222.0},
        ],
    )

    resp = await fixed_uuid_generation_client.get(
        "/v1/GB/solar/generation/period",
        params={
            "region_type": "gsp",
            "start_utc": (t_early - dt.timedelta(minutes=1)).isoformat(),
            "end_utc": (t_early + dt.timedelta(minutes=1)).isoformat(),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    regions = body["regions"]
    assert len(regions) == 1
    assert len(body["times_utc"]) == 1
    assert regions[0]["power_kW"] == [111.0]


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
    """refresh endpoint accepts non-default region_type (national) — returns 202."""
    resp = await admin_client.post(
        "/v1/GB/solar/forecasts/refresh?region_type=national",
    )
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
async def test_region_id_name_not_found_404(client: AsyncClient) -> None:
    """Non-UUID, non-matching name in region_id → 404."""
    resp = await client.get("/v1/GB/solar/regions/NATIONAL/generation")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_country_lowercase_200(client: AsyncClient) -> None:
    """Country code in lowercase is normalised to uppercase → 200."""
    resp = await client.get("/v1/gb/solar/region-types")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_forecasts_snapshot_invalid_region_type_for_country_400(
    client: AsyncClient,
) -> None:
    """province is unknown for GB → 400 (valid for NL but not GB)."""
    resp = await client.get(
        "/v1/GB/solar/forecasts/snapshot?region_type=province",
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_generation_snapshot_invalid_region_type_for_country_400(
    client: AsyncClient,
) -> None:
    """province is unknown for GB → 400 on generation snapshot too."""
    resp = await client.get(
        "/v1/GB/solar/generation/snapshot?region_type=province",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Auth — country-level access control
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_data_route_no_permissions_returns_403(
    no_perm_client: AsyncClient,
) -> None:
    """No permissions → 403 on any country-scoped data route."""
    resp = await no_perm_client.get("/v1/GB/solar/regions?region_type=gsp")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_data_route_wrong_country_returns_403() -> None:
    """NL permission only → 403 on GB data route."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["read:nl"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/v1/GB/solar/regions?region_type=gsp")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_data_route_trial_permission_grants_access(
    trial_client: AsyncClient,
) -> None:
    """read:trial bypasses country check → 200 on GB data route."""
    resp = await trial_client.get("/v1/GB/solar/regions?region_type=gsp")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_data_route_partner_permission_grants_access() -> None:
    """read:partner bypasses country check → 200 on GB data route."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(StorageClient(), ["read:partner"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
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
    """Intraday-only user gets the configured intraday default model for GSP."""
    gsp_rt = COUNTRIES["GB"].get_region_type("gsp")
    assert gsp_rt is not None and gsp_rt.intraday_default_model is not None
    expected_model = gsp_rt.intraday_default_model.api_name
    region_id = str(uuid4())
    resp = await intraday_client.get(f"/v1/GB/solar/regions/{region_id}/forecast")
    assert resp.status_code == 200
    assert resp.json()["model_name"] == expected_model


@pytest.mark.anyio
async def test_intraday_user_requesting_intraday_model_200(
    intraday_client: AsyncClient,
) -> None:
    """Intraday-only user can explicitly request any permitted intraday model → 200."""
    gsp_rt = COUNTRIES["GB"].get_region_type("gsp")
    assert gsp_rt is not None and gsp_rt.intraday_models
    # intraday_models must be a subset of forecast_models — if this fails, update the config
    assert all(m in gsp_rt.forecast_models for m in gsp_rt.intraday_models), (
        "intraday_models contains models absent from forecast_models; "
        "_validate_model will reject them with 400"
    )
    intraday_model = gsp_rt.intraday_models[0].api_name
    region_id = str(uuid4())
    resp = await intraday_client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model={intraday_model}",
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_intraday_user_requesting_non_intraday_model_403(
    intraday_client: AsyncClient,
) -> None:
    """Intraday-only user requesting a model outside their permitted set → 403."""
    gsp_rt = COUNTRIES["GB"].get_region_type("gsp")
    assert gsp_rt is not None
    allowed = gsp_rt.intraday_api_names()
    restricted_model = next(
        m.api_name for m in gsp_rt.forecast_models if m.api_name not in allowed
    )
    region_id = str(uuid4())
    resp = await intraday_client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model={restricted_model}",
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
    """NL region-types discovery is public and returns national + province."""
    resp = await nl_client.get("/v1/NL/solar/region-types")
    assert resp.status_code == 200
    types = {rt["type"] for rt in resp.json()}
    assert "national" in types
    assert "province" in types
    assert "gsp" not in types


@pytest.mark.anyio
async def test_nl_region_types_public_no_auth(no_perm_client: AsyncClient) -> None:
    """NL region-types is public — no permission required."""
    resp = await no_perm_client.get("/v1/NL/solar/region-types")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_nl_regions_requires_nl_permission(client: AsyncClient) -> None:
    """read:gb only (no read:nl) → 403 on NL data routes."""
    resp = await client.get("/v1/NL/solar/regions?region_type=national")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_nl_regions_accessible_with_read_nl(nl_client: AsyncClient) -> None:
    """read:nl grants access to NL region listing."""
    resp = await nl_client.get("/v1/NL/solar/regions?region_type=national")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_nl_regions_provinces_type(nl_client: AsyncClient) -> None:
    """province is a valid region type for NL → 200."""
    resp = await nl_client.get("/v1/NL/solar/regions?region_type=province")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_nl_regions_invalid_type_gsp_400(nl_client: AsyncClient) -> None:
    """gsp is not a valid region type for NL → 400."""
    resp = await nl_client.get("/v1/NL/solar/regions?region_type=gsp")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_nl_forecast_default_model(nl_client: AsyncClient) -> None:
    """NL national forecast returns blend as the default model."""
    resp = await nl_client.get("/v1/NL/solar/regions/national/forecast")
    assert resp.status_code == 200
    assert resp.json()["model_name"] == "blend"


@pytest.mark.anyio
async def test_nl_forecast_invalid_model_400(nl_client: AsyncClient) -> None:
    """pvnet_day_ahead is a GB-only model, not valid for NL → 400."""
    resp = await nl_client.get(
        "/v1/NL/solar/regions/national/forecast?model=pvnet_day_ahead",
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_nl_generation_sources(nl_client: AsyncClient) -> None:
    """NL generation sources discovery is public and includes ned_nl."""
    resp = await nl_client.get("/v1/NL/solar/generation-sources")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert "ned_nl" in names


@pytest.mark.anyio
async def test_nl_accessible_with_trial_permission(
    nl_trial_client: AsyncClient,
) -> None:
    """read:trial grants access to NL data routes."""
    resp = await nl_trial_client.get("/v1/NL/solar/regions?region_type=national")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_nl_forecast_snapshot(nl_client: AsyncClient) -> None:
    """NL forecast snapshot for national region type returns 200."""
    resp = await nl_client.get("/v1/NL/solar/forecasts/snapshot?region_type=national")
    assert resp.status_code == 200
    assert "time_utc" in resp.json()
    assert "values" in resp.json()


# ---------------------------------------------------------------------------
# Config invariants


def test_country_config_default_models_are_valid() -> None:
    """Every default_model set on a RegionTypeConfig must appear in forecast_models."""
    for country_code, cfg in COUNTRIES.items():
        for rt in cfg.region_types:
            if rt.default_model is None:
                continue
            model_names = {fm.name for fm in rt.forecast_models}
            assert rt.default_model in model_names, (
                f"{country_code}/{rt.type}: default_model '{rt.default_model}' "
                f"is not in forecast_models {sorted(model_names)}"
            )


def test_country_config_intraday_models_subset_of_forecast_models() -> None:
    """intraday_models must be a subset of forecast_models for every region type."""
    for country_code, cfg in COUNTRIES.items():
        for rt in cfg.region_types:
            for fm in rt.intraday_models:
                assert fm in rt.forecast_models, (
                    f"{country_code}/{rt.type}: intraday model '{fm.api_name}' "
                    f"is absent from forecast_models"
                )


def test_country_config_intraday_default_in_intraday_models() -> None:
    """A region type restricting intraday users must name a default they can have."""
    for country_code, cfg in COUNTRIES.items():
        for rt in cfg.region_types:
            if not rt.intraday_models:
                continue
            assert rt.intraday_default_model is not None, (
                f"{country_code}/{rt.type}: intraday_models is set but "
                f"intraday_default_model is not"
            )
            assert rt.intraday_default_model in rt.intraday_models, (
                f"{country_code}/{rt.type}: intraday_default_model "
                f"'{rt.intraday_default_model.api_name}' is not in intraday_models"
            )


# ---------------------------------------------------------------------------
# NL display-name filter


@pytest_asyncio.fixture
async def nl_province_client() -> AsyncGenerator[AsyncClient, None]:
    """NL client backed by NLProvinceClient for display-name filter tests."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(NLProvinceClient(), ["read:nl"])
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.mark.anyio
async def test_nl_forecast_period_display_name_filter(
    nl_province_client: AsyncClient,
) -> None:
    """region_names filter matches NL province display names, not internal DP names."""
    prefix = FastAPICache.get_prefix()
    backend = FastAPICache.get_backend()
    base = f"{prefix}:v1:period:NL:solar:province"
    await backend.set(
        f"{base}:_meta",
        json.dumps(
            {
                "model_name": "nl_blend_adjust",
                "model_version": "1",
                "last_updated_utc": None,
                "latest_init_utc": None,
                "cache_updated_utc": None,
            },
        ).encode(),
        expire=3600,
    )
    await backend.set(
        f"{base}:{_FIXED_NL_PROVINCE_UUID}",
        json.dumps(
            [
                {
                    "time_utc": "2026-01-01T12:00:00+00:00",
                    "power_kW": 100.0,
                    "plevels_kW": {},
                },
            ],
        ).encode(),
        expire=3600,
    )

    # Filter by display name — should match despite internal name being different.
    resp = await nl_province_client.get(
        f"/v1/NL/solar/forecasts/period?region_type=province&region_names={_NL_PROVINCE_DISPLAY_NAME}",
    )
    assert resp.status_code == 200
    regions = resp.json()["regions"]
    assert len(regions) == 1
    assert regions[0]["region_name"] == _NL_PROVINCE_DISPLAY_NAME

    # Filter by internal DP name — should also match (fallback).
    resp2 = await nl_province_client.get(
        f"/v1/NL/solar/forecasts/period?region_type=province&region_names={_NL_PROVINCE_INTERNAL_NAME}",
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["regions"]) == 1

# --- Eclipse adjustment (12 Aug 2026) ---

_ECLIPSE_DATE = dt.date(2026, 8, 12)
# 18:00 UTC on the eclipse date.
_ECLIPSE_FACTOR_GB = 0.566288
_ECLIPSE_FACTOR_NL = 0.420696
_ECLIPSE_POWER_KW = 10_000.0
_ECLIPSE_WINDOW = "start_utc=2026-08-12T00:00:00Z&end_utc=2026-08-13T00:00:00Z"


def _eclipse_pgv(
    location_uuid: UUID,
    hour: int,
    minute: int = 0,
) -> models.PredictedGenerationValue:
    return models.PredictedGenerationValue(
        power_kilowatts=_ECLIPSE_POWER_KW,
        valid_timestamp=dt.datetime(2026, 8, 12, hour, minute, tzinfo=dt.UTC),
        location_uuid=location_uuid,
        capacity_kilowatts=20_000.0,
        forecaster_name="blend_adjust",
        forecaster_version="1.3.0",
        plevels_kilowatts={"p10": 8_000.0, "p90": 12_000.0},
    )


class _EclipseForecastMixin:
    """Returns fixed forecast values spanning the eclipse window."""

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
        uuid = location_uuid if isinstance(location_uuid, UUID) else UUID(str(location_uuid))
        # 12:00 is clear of the eclipse, 18:00 is mid-eclipse in both countries.
        return [_eclipse_pgv(uuid, 12), _eclipse_pgv(uuid, 18)]

    async def get_predicted_generation_snapshot(  # type: ignore[override]
        self,
        location_uuids: list[UUID],
        snapshot_timestamp_utc: dt.datetime,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
        forecaster_name: str | None = None,
        forecaster_version: str | None = None,
    ) -> list[models.PredictedGenerationValue]:
        return [
            _eclipse_pgv(uuid, snapshot_timestamp_utc.hour, snapshot_timestamp_utc.minute)
            for uuid in location_uuids
        ]


class EclipseNationClient(_EclipseForecastMixin, NationResponseClient):
    """GB national location with forecast values spanning the eclipse."""


class EclipseGSPClient(_EclipseForecastMixin, FixedUUIDStorageClient):
    """GB GSP location with the same values — must come back unadjusted."""

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
                    name="Fixed GSP",
                    latitude=51.0,
                    longitude=-1.0,
                    capacity_kilowatts=76_000,
                    location_type=models.LocationType.GSP,
                ),
            ]
        return await super().get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata={},
            location_uuid=location_uuid,
            enclosing_location_uuid=enclosing_location_uuid,
        )


class EclipseNLNationClient(_EclipseForecastMixin, NationNameStorageClient):
    """NL national location with forecast values spanning the eclipse."""

    def __init__(self) -> None:
        super().__init__("nl_national")


@pytest.fixture(autouse=True)
def _pin_eclipse_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eclipse, "ECLIPSE_ENABLED", True)
    monkeypatch.setattr(eclipse, "ECLIPSE_DATE", _ECLIPSE_DATE)


async def _eclipse_client(
    db: models.StorageInterface,
    permissions: list[str],
) -> AsyncGenerator[AsyncClient, None]:
    FastAPICache.init(InMemoryBackend(), prefix="test")
    app = _make_app(db, permissions)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def eclipse_national_client() -> AsyncGenerator[AsyncClient, None]:
    async for c in _eclipse_client(EclipseNationClient(), ["read:gb"]):
        yield c


@pytest_asyncio.fixture
async def eclipse_gsp_client() -> AsyncGenerator[AsyncClient, None]:
    async for c in _eclipse_client(EclipseGSPClient(), ["read:gb"]):
        yield c


@pytest_asyncio.fixture
async def eclipse_nl_client() -> AsyncGenerator[AsyncClient, None]:
    async for c in _eclipse_client(EclipseNLNationClient(), ["read:nl"]):
        yield c


@pytest.mark.anyio
async def test_gb_national_forecast_is_eclipse_adjusted(
    eclipse_national_client: AsyncClient,
) -> None:
    resp = await eclipse_national_client.get(
        f"/v1/GB/solar/regions/{uuid4()}/forecast?{_ECLIPSE_WINDOW}",
    )
    assert resp.status_code == 200
    midday, during = resp.json()["values"]

    assert midday["power_kW"] == _ECLIPSE_POWER_KW
    assert during["power_kW"] == pytest.approx(_ECLIPSE_POWER_KW * _ECLIPSE_FACTOR_GB)
    assert during["plevels_kW"]["p10"] == pytest.approx(8_000.0 * _ECLIPSE_FACTOR_GB)
    assert during["plevels_kW"]["p90"] == pytest.approx(12_000.0 * _ECLIPSE_FACTOR_GB)


@pytest.mark.anyio
async def test_nl_national_forecast_uses_the_nl_curve(
    eclipse_nl_client: AsyncClient,
) -> None:
    resp = await eclipse_nl_client.get(
        f"/v1/NL/solar/regions/national/forecast?{_ECLIPSE_WINDOW}",
    )
    assert resp.status_code == 200
    _, during = resp.json()["values"]

    assert during["power_kW"] == pytest.approx(_ECLIPSE_POWER_KW * _ECLIPSE_FACTOR_NL)


@pytest.mark.anyio
async def test_gsp_forecast_is_not_eclipse_adjusted(
    eclipse_gsp_client: AsyncClient,
) -> None:
    resp = await eclipse_gsp_client.get(
        f"/v1/GB/solar/regions/{_FIXED_GSP_UUID}/forecast?{_ECLIPSE_WINDOW}",
    )
    assert resp.status_code == 200
    for value in resp.json()["values"]:
        assert value["power_kW"] == _ECLIPSE_POWER_KW


@pytest.mark.anyio
async def test_national_snapshot_is_eclipse_adjusted(
    eclipse_national_client: AsyncClient,
) -> None:
    resp = await eclipse_national_client.get(
        "/v1/GB/solar/forecasts/snapshot?region_type=national&time_utc=2026-08-12T18:00:00Z",
    )
    assert resp.status_code == 200
    values = resp.json()["values"]
    assert values
    for value in values:
        assert value["power_kW"] == pytest.approx(_ECLIPSE_POWER_KW * _ECLIPSE_FACTOR_GB)


# ---------------------------------------------------------------------------
# GB model naming — new slugs, retired aliases, and the trend adjuster boolean
# ---------------------------------------------------------------------------

_GB_NATIONAL_RT = COUNTRIES["GB"].get_region_type("national")
_GB_GSP_RT = COUNTRIES["GB"].get_region_type("gsp")


@pytest.mark.parametrize(
    ("api_name", "internal_name"),
    [
        ("blend", "blend"),
        ("ecmwf_mo", "pvnet_day_ahead"),
        ("ecmwf_mo_sat_8h", "pvnet_v2"),
        ("ecmwf", "pvnet_ecmwf"),
        ("sat_8h", "pvnet_sat_only"),
        ("mo", "pvnet_ukv_only"),
    ],
)
def test_gb_model_slugs_map_to_dp_names(api_name: str, internal_name: str) -> None:
    """Each new GB slug resolves to the expected internal DP forecaster name."""
    assert _GB_NATIONAL_RT is not None
    fm = _GB_NATIONAL_RT.get_model_by_api_name(api_name)
    assert fm is not None, f"'{api_name}' is not a known GB national model"
    assert fm.name == internal_name
    assert fm.adjust_name == f"{internal_name}_adjust"


def test_gb_national_advertises_only_the_six_new_names() -> None:
    """Retired aliases resolve but must not appear in the advertised model list."""
    assert _GB_NATIONAL_RT is not None
    assert {m.api_name for m in _GB_NATIONAL_RT.forecast_models} == {
        "blend",
        "ecmwf_mo",
        "ecmwf_mo_sat_8h",
        "ecmwf",
        "sat_8h",
        "mo",
    }


@pytest.mark.parametrize(
    ("legacy_name", "expected_internal"),
    [
        # Unadjusted legacy names pin the adjuster off, adjusted ones pin it on,
        # so a client on an old name keeps the exact forecast it had before.
        ("blend_adjust", "blend_adjust"),
        ("pvnet_intraday", "pvnet_v2"),
        ("pvnet_intraday_adjust", "pvnet_v2_adjust"),
        ("pvnet_day_ahead", "pvnet_day_ahead"),
        ("pvnet_day_ahead_adjust", "pvnet_day_ahead_adjust"),
        ("pvnet_ecmwf", "pvnet_ecmwf"),
        ("pvnet_ecmwf_adjust", "pvnet_ecmwf_adjust"),
        ("pvnet_sat", "pvnet_sat_only"),
        ("pvnet_sat_adjust", "pvnet_sat_only_adjust"),
        ("pvnet_ukv", "pvnet_ukv_only"),
        ("pvnet_ukv_adjust", "pvnet_ukv_only_adjust"),
    ],
)
def test_retired_gb_names_still_resolve(
    legacy_name: str,
    expected_internal: str,
) -> None:
    """Every pre-rename GB model name keeps resolving to the same DP forecaster."""
    resolved = resolve_forecast_model(legacy_name, _GB_NATIONAL_RT, False)
    assert resolved == expected_internal


@pytest.mark.parametrize(
    ("adjusted", "expected"),
    [(True, "pvnet_v2_adjust"), (False, "pvnet_v2")],
)
def test_adjusted_selects_variant(
    adjusted: bool,
    expected: str,
) -> None:
    """`adjusted` selects between a model's plain and adjusted DP forecasters."""
    resolved = resolve_forecast_model(
        "ecmwf_mo_sat_8h",
        _GB_NATIONAL_RT,
        False,
        adjusted,
    )
    assert resolved == expected


def test_adjusted_defaults_on() -> None:
    """Omitting the model and the boolean gives the adjusted default forecaster."""
    assert resolve_forecast_model(None, _GB_NATIONAL_RT, False) == "blend_adjust"


def test_adjusted_is_noop_for_gsp() -> None:
    """GSP has no adjusted DP variants, so the boolean must not alter the name."""
    assert _GB_GSP_RT is not None
    assert not _GB_GSP_RT.supports_adjusted
    for flag in (True, False):
        assert resolve_forecast_model("blend", _GB_GSP_RT, False, flag) == "blend"
        assert resolve_forecast_model(None, _GB_GSP_RT, False, flag) == "blend"


def test_cache_warms_the_adjusted_default() -> None:
    """The pre-warmed period cache must use the adjusted default where one exists."""
    assert _GB_NATIONAL_RT is not None and _GB_GSP_RT is not None
    assert _GB_NATIONAL_RT.default_forecaster_name() == "blend_adjust"
    assert _GB_GSP_RT.default_forecaster_name() == "blend"


@pytest.mark.anyio
@pytest.mark.parametrize("model", ["ecmwf_mo_sat_8h", "mo", "pvnet_intraday"])
async def test_national_forecast_accepts_new_and_legacy_names(
    nation_response_client: AsyncClient,
    model: str,
) -> None:
    """New slugs and retired aliases are both accepted on the forecast route."""
    region_id = str(uuid4())
    resp = await nation_response_client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model={model}",
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_national_forecast_rejects_unknown_model(
    nation_response_client: AsyncClient,
) -> None:
    """A name that is neither a current slug nor an alias is still a 400."""
    region_id = str(uuid4())
    resp = await nation_response_client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model=not_a_model",
    )
    assert resp.status_code == 400
    # Only the current names are advertised back to the caller.
    assert "pvnet" not in resp.json()["detail"]


@pytest.mark.anyio
async def test_region_types_expose_supports_adjusted(client: AsyncClient) -> None:
    """Clients can discover where the `adjusted` param applies without reading docs."""
    resp = await client.get("/v1/GB/solar/region-types")
    assert resp.status_code == 200
    by_type = {rt["type"]: rt for rt in resp.json()}
    assert by_type["national"]["supports_adjusted"] is True
    assert by_type["gsp"]["supports_adjusted"] is False


def test_intraday_default_never_falls_back_to_unrestricted_model() -> None:
    """A missing intraday_default_model must not leak the unrestricted default."""
    rt = RegionTypeConfig(
        type="gsp",
        label="Grid Supply Point",
        level=10,
        location_type=models.LocationType.GSP,
        forecast_models=(FM.BLEND, FM.ECMWF_MO_SAT_8H),
        default_model="blend",
        intraday_models=(FM.ECMWF_MO_SAT_8H,),
        intraday_default_model=None,
    )
    resolved = resolve_forecast_model(None, rt, is_intraday_only=True)
    assert resolved == "pvnet_v2", "intraday-only caller received the unrestricted default"


def test_no_configured_default_sends_no_forecaster_filter() -> None:
    """A region type with no default_model resolves to None, letting the DP choose."""
    rt = RegionTypeConfig(
        type="dno",
        label="DNO",
        level=5,
        location_type=models.LocationType.DNO,
    )
    assert resolve_forecast_model(None, rt, is_intraday_only=False) is None


@pytest.mark.anyio
@pytest.mark.parametrize("model", ["blend_adjust", "pvnet_intraday_adjust"])
async def test_gsp_rejects_adjust_aliases(client: AsyncClient, model: str) -> None:
    """GSP has no adjusted variants, so an `_adjust` name is a 400, not silent fallback."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model={model}",
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_national_still_accepts_adjust_aliases(
    nation_response_client: AsyncClient,
) -> None:
    """National does have adjusted variants, so the same names keep working there."""
    region_id = str(uuid4())
    resp = await nation_response_client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast?model=blend_adjust",
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_last_updated_rejects_unknown_model(client: AsyncClient) -> None:
    """last-updated validates the model rather than passing it to the backend."""
    region_id = str(uuid4())
    resp = await client.get(
        f"/v1/GB/solar/regions/{region_id}/forecast/last-updated?model=not_a_model",
    )
    assert resp.status_code == 400


_NL_NATIONAL_RT = COUNTRIES["NL"].get_region_type("national")


def test_nl_advertises_the_renamed_uncurtailed_slug() -> None:
    """The PV-inclusive name is the advertised one; the old name is not."""
    assert _NL_NATIONAL_RT is not None
    advertised = {m.api_name for m in _NL_NATIONAL_RT.forecast_models}
    assert advertised == {"blend", "ecmwf_mo_pv_sat_uncurtailed"}


@pytest.mark.parametrize(
    ("legacy_name", "expected_internal"),
    [
        # The old name pins the adjuster off, so it returns what it did before the
        # rename rather than picking up the new adjusted-by-default.
        ("ecmwf_mo_sat_uncurtailed", "nl_regional_pv_ecmwf_mo_sat_uncurtailed"),
        (
            "ecmwf_mo_sat_uncurtailed_adjust",
            "nl_regional_pv_ecmwf_mo_sat_uncurtailed_adjust",
        ),
        ("blend_adjust", "nl_blend_adjust"),
    ],
)
def test_retired_nl_names_still_resolve(
    legacy_name: str,
    expected_internal: str,
) -> None:
    """Every pre-rename NL name keeps resolving to the same DP forecaster."""
    assert resolve_forecast_model(legacy_name, _NL_NATIONAL_RT, False) == expected_internal
