"""Tests for QuartzDBClient methods."""

# ruff: noqa: ARG002
import datetime as dt
import logging

import pytest
from fastapi import HTTPException
from pvsite_datamodel.sqlmodels import GenerationSQL, LocationSQL
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import EMAIL_KEY

from .client import StorageClient

log = logging.getLogger(__name__)


@pytest.fixture()
def client(engine: Engine, db_session: Session) -> StorageClient:
    """Hooks StorageClient into pytest db_session fixture"""
    client = StorageClient(database_url=str(engine.url))
    client.session = db_session

    return client


class TestQuartzDBClient:
    @pytest.mark.asyncio
    async def test_get_predicted_wind_power_production_for_region(
        self,
        client: StorageClient,
        forecast_values_wind: None,
    ) -> None:
        locID = "testID"
        result = await client.get_predicted_generation(
            location_uuid=locID,
            window_start=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2),
            window_end=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2),
            energy_type=models.EnergyType.WIND,
            location_type=models.LocationType.REGION,
            authdata={},
        )

        assert len(result) == 110

    @pytest.mark.asyncio
    async def test_get_predicted_wind_power_production_for_region_raise_error(
        self,
        client: StorageClient,
        forecast_values_wind: None,
    ) -> None:
        with pytest.raises(HTTPException):
            _ = await client.get_predicted_generation(
                location_uuid="testID2",
                window_start=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2),
                window_end=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2),
                energy_type=models.EnergyType.WIND,
                location_type=models.LocationType.REGION,
                authdata={},
            )

    @pytest.mark.asyncio
    async def test_get_predicted_solar_power_production_for_region(
        self,
        client: StorageClient,
        forecast_values: None,
    ) -> None:
        locID = "testID"
        result = await client.get_predicted_generation(
            location_uuid=locID,
            window_start=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2),
            window_end=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2),
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.REGION,
            authdata={},
        )

        assert len(result) == 110

    @pytest.mark.asyncio
    async def test_get_actual_wind_power_production_for_region(
        self,
        client: StorageClient,
        generations: list[GenerationSQL],
    ) -> None:
        locID = "testID"
        result = await client.get_actual_generation(
            location_uuid=locID,
            window_start=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2),
            window_end=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2),
            energy_type=models.EnergyType.WIND,
            location_type=models.LocationType.REGION,
            authdata={},
        )

        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_get_actual_solar_power_production_for_region(
        self,
        client: StorageClient,
        generations: list[GenerationSQL],
    ) -> None:
        locID = "testID"
        result = await client.get_actual_generation(
            location_uuid=locID,
            window_start=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2),
            window_end=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2),
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.REGION,
            authdata={},
        )

        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_get_wind_regions(self, client: StorageClient) -> None:
        result = await client.get_locations(
            energy_type=models.EnergyType.WIND,
            location_type=models.LocationType.REGION,
            authdata={},
        )
        assert len(result) == 1
        assert result[0].name == "ruvnl"

    @pytest.mark.asyncio
    async def test_get_solar_regions(self, client: StorageClient) -> None:
        result = await client.get_locations(
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.REGION,
            authdata={},
        )
        assert len(result) == 1
        assert result[0].name == "ruvnl"

    @pytest.mark.asyncio
    async def test_get_sites(self, client: StorageClient, sites: list[LocationSQL]) -> None:
        sites_from_api = await client.get_locations(
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.SITE,
            authdata={EMAIL_KEY: "test@test.com"},
        )
        assert len(sites_from_api) == 2

    @pytest.mark.asyncio
    async def test_get_sites_no_sites(
        self,
        client: StorageClient,
        sites: list[LocationSQL],
    ) -> None:
        sites_from_api = await client.get_locations(
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.SITE,
            authdata={EMAIL_KEY: "test2@test.com"},
        )
        assert len(sites_from_api) == 0

    @pytest.mark.asyncio
    async def test_get_put_site(self, client: StorageClient, sites: list[LocationSQL]) -> None:
        sites_from_api = await client.get_locations(
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.SITE,
            authdata={EMAIL_KEY: "test@test.com"},
        )
        assert sites_from_api[0].metadata["client_site_name"] == "ruvnl_pv_testID1"
        site = await client.put_location(
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.SITE,
            authdata={EMAIL_KEY: "test@test.com"},
            location=models.Location(
                uuid=sites[0].location_uuid,
                name="test_zzz",
                latitude=12.34,
                longitude=56.78,
                capacity_kilowatts=100.0,
                metadata={
                    "orientation": 180.0,
                    "tilt": 30.0,
                    "client_site_name": "test_zzz",
                },
            ),
        )
        assert site.name == "test_zzz"
        assert site.latitude is not None

    @pytest.mark.asyncio
    async def test_get_site_forecast(
        self,
        client: StorageClient,
        sites: list[LocationSQL],
        forecast_values_site: None,
    ) -> None:
        out = await client.get_predicted_generation(
            location_uuid=sites[0].location_uuid,
            window_start=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2),
            window_end=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2),
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.SITE,
            authdata={EMAIL_KEY: "test@test.com"},
        )
        assert len(out) > 0

    @pytest.mark.asyncio
    async def test_get_site_forecast_no_forecast_values(
        self,
        client: StorageClient,
        sites: list[LocationSQL],
    ) -> None:
        out = await client.get_predicted_generation(
            location_uuid=sites[0].location_uuid,
            window_start=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2),
            window_end=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2),
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.SITE,
            authdata={EMAIL_KEY: "test@test.com"},
        )
        assert len(out) == 0

    @pytest.mark.asyncio
    async def test_get_site_forecast_no_access(
        self,
        client: StorageClient,
        sites: list[LocationSQL],
    ) -> None:
        with pytest.raises(HTTPException):
            _ = await client.get_predicted_generation(
                location_uuid=sites[0].location_uuid,
                window_start=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2),
                window_end=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2),
                energy_type=models.EnergyType.SOLAR,
                location_type=models.LocationType.SITE,
                authdata={EMAIL_KEY: "test2@test.com"},
            )

    @pytest.mark.asyncio
    async def test_get_site_generation(
        self,
        client: StorageClient,
        sites: list[LocationSQL],
        generations: list[GenerationSQL],
    ) -> None:
        out = await client.get_actual_generation(
            location_uuid=sites[0].location_uuid,
            window_start=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2),
            window_end=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2),
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.SITE,
            authdata={EMAIL_KEY: "test@test.com"},
        )
        assert len(out) > 0

    @pytest.mark.asyncio
    async def test_post_site_generation(
        self,
        client: StorageClient,
        sites: list[LocationSQL],
    ) -> None:
        await client.put_actual_generation(
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.SITE,
            authdata={EMAIL_KEY: "test@test.com"},
            generation_values=[
                models.ActualGenerationValue(
                    power_kilowatts=1.0,
                    valid_timestamp=dt.datetime(2021, 1, 1, tzinfo=dt.UTC),
                    location_uuid=sites[0].location_uuid,
                    capacity_kilowatts=100.0,
                    observer_name="unit_test",
                ),
            ],
        )

    @pytest.mark.asyncio
    async def test_post_site_generation_exceding_max_capacity(
        self,
        client: StorageClient,
        sites: list[LocationSQL],
    ) -> None:
        try:
            await client.put_actual_generation(
                energy_type=models.EnergyType.SOLAR,
                location_type=models.LocationType.SITE,
                authdata={EMAIL_KEY: "test@test.com"},
                generation_values=[
                    models.ActualGenerationValue(
                        power_kilowatts=200.0,
                        valid_timestamp=dt.datetime(2021, 1, 1, tzinfo=dt.UTC),
                        location_uuid=sites[0].location_uuid,
                        capacity_kilowatts=100.0,
                        observer_name="unit_test",
                    ),
                ],
            )
        except HTTPException as e:
            assert e.status_code == 422
            assert "generation values" in str(e.detail)
