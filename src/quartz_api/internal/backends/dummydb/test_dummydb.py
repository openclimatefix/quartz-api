import datetime as dt
import unittest
import uuid

from quartz_api.internal.middleware.auth import EMAIL_KEY
from quartz_api.internal.models import ActualPower

from .client import Client

client = Client()


class TestDummyDatabase(unittest.IsolatedAsyncioTestCase):
    async def test_get_predicted_wind_power_production_for_location(self) -> None:
        out = await client.get_predicted_wind_power_production_for_location("testID")
        self.assertIsNotNone(out)

    async def test_get_predicted_solar_power_production_for_location(self) -> None:
        out = await client.get_predicted_solar_power_production_for_location("testID")
        self.assertIsNotNone(out)

    async def test_get_actual_wind_power_production_for_location(self) -> None:
        out = await client.get_actual_wind_power_production_for_location("testID")
        self.assertIsNotNone(out)

    async def test_get_actual_solar_power_production_for_location(self) -> None:
        out = await client.get_actual_solar_power_production_for_location("testID")
        self.assertIsNotNone(out)

    async def test_get_wind_regions(self) -> None:
        out = await client.get_wind_regions()
        self.assertIsNotNone(out)

    async def test_get_solar_regions(self) -> None:
        out = await client.get_solar_regions()
        self.assertIsNotNone(out)

    async def test_get_sites(self) -> None:
        out = await client.get_sites(authdata={EMAIL_KEY: "test-test@test.com"})
        self.assertIsNotNone(out)

    async def test_get_site_forecast(self) -> None:
        out = await client.get_site_forecast(site_uuid=uuid.uuid4(), authdata={})
        self.assertIsNotNone(out)

    async def test_get_site_generation(self) -> None:
        out = await client.get_site_generation(site_uuid=uuid.uuid4(), authdata={})
        self.assertIsNotNone(out)

    async def test_post_site_generation(self) -> None:
        await client.post_site_generation(
            site_uuid=uuid.uuid4(),
            generation=[ActualPower(Time=dt.datetime(2021, 1, 1, tzinfo=dt.UTC), PowerKW=1)],
            authdata={},
        )
