"""Tests for QuartzDBClient methods."""

# ruff: noqa: ARG002
import logging

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from quartz_api.internal import ActualPower, PredictedPower, SiteProperties
from quartz_api.internal.middleware.auth import EMAIL_KEY

from .client import Client

log = logging.getLogger(__name__)

# TODO add list of test that are here


@pytest.fixture()
def client(engine: Engine, db_session: Session) -> Client:
    """Hooks Client into pytest db_session fixture"""
    client = Client(database_url=str(engine.url))
    client.session = db_session

    return client


class TestQuartzDBClient:
    @pytest.mark.asyncio
    async def test_get_predicted_wind_power_production_for_location(
        self,
        client: Client,
        forecast_values_wind: None,
    ) -> None:
        locID = "testID"
        result = await client.get_predicted_wind_power_production_for_location(locID)

        assert len(result) == 110
        for record in result:
            assert isinstance(record, PredictedPower)

    @pytest.mark.asyncio
    async def test_get_predicted_wind_power_production_for_location_raise_error(
        self,
        client,
        forecast_values_wind,
    ) -> None:
        with pytest.raises(HTTPException):
            _ = await client.get_predicted_wind_power_production_for_location("testID2")

    @pytest.mark.asyncio
    async def test_get_predicted_solar_power_production_for_location(
        self,
        client,
        forecast_values,
    ) -> None:
        locID = "testID"
        result = await client.get_predicted_solar_power_production_for_location(locID)

        assert len(result) == 110
        for record in result:
            assert isinstance(record, PredictedPower)

    @pytest.mark.asyncio
    async def test_get_actual_wind_power_production_for_location(self, client, generations) -> None:
        locID = "testID"
        result = await client.get_actual_wind_power_production_for_location(locID)

        assert len(result) == 10
        for record in result:
            assert isinstance(record, ActualPower)

    @pytest.mark.asyncio
    async def test_get_actual_solar_power_production_for_location(
        self,
        client,
        generations,
    ) -> None:
        locID = "testID"
        result = await client.get_actual_solar_power_production_for_location(locID)

        assert len(result) == 10
        for record in result:
            assert isinstance(record, ActualPower)

    @pytest.mark.asyncio
    async def test_get_wind_regions(self, client) -> None:
        result = await client.get_wind_regions()
        assert len(result) == 1
        assert result[0] == "ruvnl"

    @pytest.mark.asyncio
    async def test_get_solar_regions(self, client) -> None:
        result = await client.get_solar_regions()
        assert len(result) == 1
        assert result[0] == "ruvnl"

    @pytest.mark.asyncio
    async def test_get_sites(self, client, sites) -> None:
        sites_from_api = await client.get_sites(authdata={EMAIL_KEY: "test@test.com"})
        assert len(sites_from_api) == 2

    @pytest.mark.asyncio
    async def test_get_sites_no_sites(self, client, sites) -> None:
        sites_from_api = await client.get_sites(authdata={EMAIL_KEY: "test2@test.com"})
        assert len(sites_from_api) == 0

    @pytest.mark.asyncio
    async def test_get_put_site(self, client, sites) -> None:
        sites_from_api = await client.get_sites(authdata={EMAIL_KEY: "test@test.com"})
        assert sites_from_api[0].client_site_name == "ruvnl_pv_testID1"
        site = await client.put_site(
            site_uuid=sites[0].location_uuid,
            site_properties=SiteProperties(client_site_name="test_zzz"),
            authdata={EMAIL_KEY: "test@test.com"},
        )
        assert site.client_location_name == "test_zzz"
        assert site.latitude is not None

    @pytest.mark.asyncio
    async def test_get_site_forecast(self, client, sites, forecast_values_site) -> None:
        out = await client.get_site_forecast(
            site_uuid=str(sites[0].location_uuid),
            authdata={EMAIL_KEY: "test@test.com"},
        )
        assert len(out) > 0

    @pytest.mark.asyncio
    async def test_get_site_forecast_no_forecast_values(self, client, sites) -> None:
        out = await client.get_site_forecast(
            site_uuid=sites[0].location_uuid,
            authdata={EMAIL_KEY: "test@test.com"},
        )
        assert len(out) == 0

    @pytest.mark.asyncio
    async def test_get_site_forecast_no_access(self, client, sites) -> None:
        with pytest.raises(HTTPException):
            _ = await client.get_site_forecast(
                site_uuid=sites[0].location_uuid,
                authdata={EMAIL_KEY: "test2@test.com"},
            )

    @pytest.mark.asyncio
    async def test_get_site_generation(self, client, sites, generations) -> None:
        out = await client.get_site_generation(
            site_uuid=str(sites[0].location_uuid),
            authdata={EMAIL_KEY: "test@test.com"},
        )
        assert len(out) > 0

    @pytest.mark.asyncio
    async def test_post_site_generation(self, client, sites) -> None:
        await client.post_site_generation(
            site_uuid=sites[0].location_uuid,
            generation=[ActualPower(Time=1, PowerKW=1)],
            authdata={EMAIL_KEY: "test@test.com"},
        )

    @pytest.mark.asyncio
    async def test_post_site_generation_exceding_max_capacity(self, client, sites):
        try:
            await client.post_site_generation(
                site_uuid=sites[0].location_uuid,
                generation=[ActualPower(Time=1, PowerKW=1000)],
                authdata={EMAIL_KEY: "test@test.com"},
            )
        except HTTPException as e:
            assert e.status_code == 422
            assert "generation values" in str(e.detail)
