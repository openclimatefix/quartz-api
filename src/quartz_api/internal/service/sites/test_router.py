"""Unit tests for the sites router endpoints.

Tests cover:
- GET /sites          — lists all SOLAR + WIND sites for the authenticated org
- GET /sites/{uuid}/forecast   — returns forecast for a valid site UUID
- GET /sites/{uuid}/generation — returns generation data for a valid site UUID
- GET /sites/{invalid}/forecast   — returns 404 for an unknown site UUID
- GET /sites/{invalid}/generation — returns 404 for an unknown site UUID
- POST /sites/{uuid}/generation   — persists generation data, returns 200
- PUT  /sites/{uuid}              — updates site properties, returns 200
- _resolve_observer_name          — unit tests for the observer resolution helper
"""

# ruff: noqa: ARG002

import typing
from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from quartz_api.internal import models
from quartz_api.internal.backends.dummydb.client import StorageClient
from quartz_api.internal.middleware.auth import AuthDependency

from .router import _resolve_observer_name, router

_auth_dep = typing.get_args(AuthDependency)[1].dependency

# A fixed UUID used across tests so UUID-based lookups are predictable
_WIND_SITE_UUID = uuid4()
_SOLAR_SITE_UUID = uuid4()
_UNKNOWN_UUID = uuid4()


# ---------------------------------------------------------------------------
# Mock storage clients
# ---------------------------------------------------------------------------


class SitesStorageClient(StorageClient):
    """DummyDB variant that returns deterministic WIND + SOLAR sites.

    When energy_type=None (no filter), returns both energy types so the
    router's _get_site_with_energy_type() helper can detect the type.
    When filtering by UUID, returns only the matching site.
    """

    async def get_locations(  # type: ignore[override]
        self,
        energy_type: models.EnergyType | None,
        location_type: models.LocationType | None,
        authdata: dict,
        location_uuid: UUID | None = None,
        enclosing_location_uuid: UUID | None = None,
        location_names: list[str] | None = None,
    ) -> list[models.Location]:
        if location_type == models.LocationType.SITE and energy_type is None:
            all_sites = [
                models.Location(
                    uuid=_WIND_SITE_UUID,
                    name="ad_seci_5",
                    latitude=23.25,
                    longitude=69.25,
                    capacity_kilowatts=300_000,
                    location_type=models.LocationType.SITE,
                    energy_type=models.EnergyType.WIND,
                ),
                models.Location(
                    uuid=_SOLAR_SITE_UUID,
                    name="ind_rajasthan_test_farm",
                    latitude=26.0,
                    longitude=76.0,
                    capacity_kilowatts=5_000,
                    location_type=models.LocationType.SITE,
                    energy_type=models.EnergyType.SOLAR,
                ),
            ]
            if location_uuid is not None:
                return [s for s in all_sites if s.uuid == location_uuid]
            return all_sites
        return await super().get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata={},
            location_uuid=location_uuid,
            enclosing_location_uuid=enclosing_location_uuid,
            location_names=location_names,
        )


class EmptySitesClient(SitesStorageClient):
    """Returns an empty list for any SITE + energy_type=None query.

    Simulates "no sites found" (e.g., unknown UUID or wrong org).
    """

    async def get_locations(  # type: ignore[override]
        self,
        energy_type: models.EnergyType | None,
        location_type: models.LocationType | None,
        authdata: dict,
        location_uuid: UUID | None = None,
        enclosing_location_uuid: UUID | None = None,
        location_names: list[str] | None = None,
    ) -> list[models.Location]:
        if location_type == models.LocationType.SITE and energy_type is None:
            return []
        return await super().get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata=authdata,
            location_uuid=location_uuid,
            enclosing_location_uuid=enclosing_location_uuid,
            location_names=location_names,
        )


# ---------------------------------------------------------------------------
# App factory + fixtures
# ---------------------------------------------------------------------------


def _make_app(db: models.StorageInterface) -> FastAPI:
    """Build a minimal FastAPI app wired to the sites router with dummy auth."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[models.get_storage_client] = lambda: db
    app.dependency_overrides[_auth_dep] = lambda: {
        "sub": "google-oauth2|108386900569835961988",
        "app_metadata": {"hubspot_company_id": "99999999999"},
        "permissions": ["read:india"],
    }
    return app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Test client backed by SitesStorageClient — returns WIND + SOLAR sites."""
    app = _make_app(SitesStorageClient())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def empty_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client that returns no sites for any query — simulates unknown org."""
    app = _make_app(EmptySitesClient())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# GET /sites
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_sites_returns_200(client: AsyncClient) -> None:
    """GET /sites returns HTTP 200."""
    resp = await client.get("/sites")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_sites_returns_list(client: AsyncClient) -> None:
    """GET /sites returns a JSON list."""
    resp = await client.get("/sites")
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_sites_returns_both_solar_and_wind(client: AsyncClient) -> None:
    """GET /sites returns sites of both energy types."""
    resp = await client.get("/sites")
    sites = resp.json()
    assert len(sites) == 2
    names = {s["client_site_name"] for s in sites}
    assert "ad_seci_5" in names
    assert "ind_rajasthan_test_farm" in names


@pytest.mark.anyio
async def test_get_sites_has_required_fields(client: AsyncClient) -> None:
    """Each site in GET /sites has all required fields."""
    resp = await client.get("/sites")
    for site in resp.json():
        assert "site_uuid" in site
        assert "latitude" in site
        assert "longitude" in site
        assert "capacity_kW" in site
        assert "client_site_name" in site


@pytest.mark.anyio
async def test_get_sites_empty_for_unknown_org(empty_client: AsyncClient) -> None:
    """GET /sites returns an empty list when no sites match the org."""
    resp = await empty_client.get("/sites")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /sites/{uuid}/forecast — valid UUID
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_forecast_valid_wind_site_returns_200(client: AsyncClient) -> None:
    """GET /sites/{uuid}/forecast returns 200 for a known WIND site UUID."""
    resp = await client.get(f"/sites/{_WIND_SITE_UUID}/forecast")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_forecast_valid_solar_site_returns_200(client: AsyncClient) -> None:
    """GET /sites/{uuid}/forecast returns 200 for a known SOLAR site UUID."""
    resp = await client.get(f"/sites/{_SOLAR_SITE_UUID}/forecast")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_forecast_returns_list_of_values(client: AsyncClient) -> None:
    """GET /sites/{uuid}/forecast returns a non-empty list of forecast values."""
    resp = await client.get(f"/sites/{_WIND_SITE_UUID}/forecast")
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0


@pytest.mark.anyio
async def test_get_forecast_values_have_required_fields(client: AsyncClient) -> None:
    """Each forecast entry has PowerKW and Time fields."""
    resp = await client.get(f"/sites/{_WIND_SITE_UUID}/forecast")
    for entry in resp.json():
        assert "PowerKW" in entry
        assert "Time" in entry


# ---------------------------------------------------------------------------
# GET /sites/{uuid}/forecast — invalid UUID → 404
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_forecast_unknown_uuid_returns_404(empty_client: AsyncClient) -> None:
    """GET /sites/{uuid}/forecast returns 404 for an unknown site UUID."""
    resp = await empty_client.get(f"/sites/{_UNKNOWN_UUID}/forecast")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_forecast_404_contains_uuid_in_detail(empty_client: AsyncClient) -> None:
    """The 404 detail message includes the requested UUID."""
    resp = await empty_client.get(f"/sites/{_UNKNOWN_UUID}/forecast")
    assert str(_UNKNOWN_UUID) in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /sites/{uuid}/generation — valid UUID
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_generation_valid_wind_site_returns_200(client: AsyncClient) -> None:
    """GET /sites/{uuid}/generation returns 200 for a known WIND site UUID."""
    resp = await client.get(f"/sites/{_WIND_SITE_UUID}/generation")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_generation_valid_solar_site_returns_200(client: AsyncClient) -> None:
    """GET /sites/{uuid}/generation returns 200 for a known SOLAR site UUID."""
    resp = await client.get(f"/sites/{_SOLAR_SITE_UUID}/generation")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_generation_returns_list(client: AsyncClient) -> None:
    """GET /sites/{uuid}/generation returns a list."""
    resp = await client.get(f"/sites/{_WIND_SITE_UUID}/generation")
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_get_generation_values_have_required_fields(client: AsyncClient) -> None:
    """Each generation entry has PowerKW and Time fields."""
    resp = await client.get(f"/sites/{_WIND_SITE_UUID}/generation")
    for entry in resp.json():
        assert "PowerKW" in entry
        assert "Time" in entry


# ---------------------------------------------------------------------------
# GET /sites/{uuid}/generation — invalid UUID → 404
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_generation_unknown_uuid_returns_404(empty_client: AsyncClient) -> None:
    """GET /sites/{uuid}/generation returns 404 for an unknown site UUID."""
    resp = await empty_client.get(f"/sites/{_UNKNOWN_UUID}/generation")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_generation_404_contains_uuid_in_detail(empty_client: AsyncClient) -> None:
    """The 404 detail message includes the requested UUID."""
    resp = await empty_client.get(f"/sites/{_UNKNOWN_UUID}/generation")
    assert str(_UNKNOWN_UUID) in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /sites/{uuid}/generation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_post_generation_valid_site_returns_200(client: AsyncClient) -> None:
    """POST /sites/{uuid}/generation returns 200 for a known site UUID."""
    payload = [
        {"PowerKW": 150.5, "Time": "2026-06-19T08:00:00Z"},
        {"PowerKW": 175.0, "Time": "2026-06-19T08:15:00Z"},
    ]
    resp = await client.post(f"/sites/{_WIND_SITE_UUID}/generation", json=payload)
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_post_generation_unknown_uuid_returns_404(empty_client: AsyncClient) -> None:
    """POST /sites/{uuid}/generation returns 404 for an unknown site UUID."""
    payload = [{"PowerKW": 100.0, "Time": "2026-06-19T08:00:00Z"}]
    resp = await empty_client.post(f"/sites/{_UNKNOWN_UUID}/generation", json=payload)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /sites/{uuid} — update site info
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_put_site_valid_uuid_returns_200(client: AsyncClient) -> None:
    """PUT /sites/{uuid} returns 200 for a known site UUID."""
    payload = {
        "latitude": 23.25,
        "longitude": 69.25,
        "capacity_kW": 300_000,
        "client_site_name": "ad_seci_5_updated",
        "metadata": {},
    }
    resp = await client.put(f"/sites/{_WIND_SITE_UUID}", json=payload)
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_put_site_unknown_uuid_returns_404(empty_client: AsyncClient) -> None:
    """PUT /sites/{uuid} returns 404 for an unknown site UUID."""
    payload = {
        "latitude": 0.0,
        "longitude": 0.0,
        "capacity_kW": 1000,
        "client_site_name": "ghost_site",
        "metadata": {},
    }
    resp = await empty_client.put(f"/sites/{_UNKNOWN_UUID}", json=payload)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# _resolve_observer_name — unit tests
# ---------------------------------------------------------------------------


def _make_site(name: str, energy_type: models.EnergyType) -> models.Location:
    """Build a minimal Location for observer-resolution tests."""
    return models.Location(
        uuid=uuid4(),
        name=name,
        latitude=0.0,
        longitude=0.0,
        capacity_kilowatts=100.0,
        energy_type=energy_type,
    )


def test_resolve_observer_ind_solar() -> None:
    """ind_-prefixed solar site resolves to 'ind_rajasthan'."""
    site = _make_site("ind_rajasthan_farm_1", models.EnergyType.SOLAR)
    assert _resolve_observer_name(site, models.EnergyType.SOLAR) == "ind_rajasthan"


def test_resolve_observer_ind_wind() -> None:
    """ind_-prefixed wind site resolves to 'ind_rajasthan'."""
    site = _make_site("ind_rajasthan_wind_park", models.EnergyType.WIND)
    assert _resolve_observer_name(site, models.EnergyType.WIND) == "ind_rajasthan"


def test_resolve_observer_adani_prefix() -> None:
    """ad_-prefixed site (Adani) resolves to India observer 'ind_rajasthan'."""
    site = _make_site("ad_seci_5", models.EnergyType.WIND)
    assert _resolve_observer_name(site, models.EnergyType.WIND) == "ind_rajasthan"


def test_resolve_observer_unknown_prefix_uses_fallback() -> None:
    """A site with an unrecognised prefix falls back to the default."""
    site = _make_site("gb_national", models.EnergyType.SOLAR)
    result = _resolve_observer_name(site, models.EnergyType.SOLAR, fallback="fallback_observer")
    assert result == "fallback_observer"


def test_resolve_observer_default_fallback_value() -> None:
    """The built-in fallback is 'ind_rajasthan' when no explicit fallback is given."""
    site = _make_site("unknown_xyz", models.EnergyType.SOLAR)
    assert _resolve_observer_name(site, models.EnergyType.SOLAR) == "ind_rajasthan"

