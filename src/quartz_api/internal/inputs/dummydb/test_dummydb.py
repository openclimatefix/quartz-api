import unittest

from .client import Client

from quartz_api.internal import ActualPower

client = Client()


class TestDummyDatabase(unittest.TestCase):
    def test_get_predicted_wind_power_production_for_location(self) -> None:
        locID = "testID"
        out = client.get_predicted_wind_power_production_for_location(locID)
        self.assertIsNotNone(out)

    def test_get_predicted_solar_power_production_for_location(self) -> None:
        locID = "testID"
        out = client.get_predicted_solar_power_production_for_location(locID)
        self.assertIsNotNone(out)

    def test_get_actual_wind_power_production_for_location(self) -> None:
        locID = "testID"
        out = client.get_actual_wind_power_production_for_location(locID)
        self.assertIsNotNone(out)

    def test_get_actual_solar_power_production_for_location(self) -> None:
        locID = "testID"
        out = client.get_actual_solar_power_production_for_location(locID)
        self.assertIsNotNone(out)

    def test_get_wind_regions(self) -> None:
        out = client.get_wind_regions()
        self.assertIsNotNone(out)

    def test_get_solar_regions(self) -> None:
        out = client.get_solar_regions()
        self.assertIsNotNone(out)

    def test_get_sites(self) -> None:
        out = client.get_sites(email="test-test@test.com")
        self.assertIsNotNone(out)

    def test_get_site_forecast(self) -> None:
        out = client.get_site_forecast(site_uuid="testID")
        self.assertIsNotNone(out)

    def test_get_site_generation(self) -> None:
        out = client.get_site_generation(site_uuid="testID")
        self.assertIsNotNone(out)

    def test_post_site_generation(self) -> None:
        client.post_site_generation(
            site_uuid="testID", generation=[ActualPower(Time=1, PowerKW=1)]
        )
