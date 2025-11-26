import dataclasses
import datetime as dt
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from betterproto.lib.google.protobuf import Struct, Value
from dp_sdk.ocf import dp
from fastapi import HTTPException

from .client import Client


def mock_list_locations(req: dp.ListLocationsRequest) -> dp.ListLocationsResponse:
    if req.user_oauth_id_filter == "access_user":
        return dp.ListLocationsResponse(
            locations=[
                dp.ListLocationsResponseLocationSummary(
                    location_name="mock_location",
                    location_uuid=str(uuid.uuid4()),
                    energy_source=dp.EnergySource.SOLAR,
                    effective_capacity_watts=1e6,
                    location_type=dp.LocationType.SITE,
                    latlng=dp.LatLng(51.5, -0.1),
                    metadata=Struct(
                        fields={
                            "orientation": Value(number_value=180.0),
                            "tilt": Value(number_value=30.0),
                        },
                    ),
                ),
            ],
        )
    else:
        return dp.ListLocationsResponse(locations=[])


def mock_get_forecast(
    _: dp.GetForecastAsTimeseriesRequest,
) -> dp.GetForecastAsTimeseriesResponse:
    return dp.GetForecastAsTimeseriesResponse(
        values=[
            dp.GetForecastAsTimeseriesResponseValue(
                target_timestamp_utc=dt.datetime(2024, 1, 1, i, 0, 0, tzinfo=dt.UTC),
                p50_value_fraction=0.5,
                effective_capacity_watts=1e6,
                initialization_timestamp_utc=dt.datetime(2023, 12, 31, 23, 0, 0, tzinfo=dt.UTC),
                created_timestamp_utc=dt.datetime(2023, 12, 31, 22, 49, 0, tzinfo=dt.UTC),
                other_statistics_fractions={"p90": 0.9, "p10": 0.1},
                metadata=Struct(fields={}),
            )
            for i in range(5)
        ],
    )


def mock_get_observations(
    _: dp.GetObservationsAsTimeseriesRequest,
) -> dp.GetObservationsAsTimeseriesResponse:
    return dp.GetObservationsAsTimeseriesResponse(
        values=[
            dp.GetObservationsAsTimeseriesResponseValue(
                timestamp_utc=dt.datetime(2024, 1, 1, i, 0, 0, tzinfo=dt.UTC),
                value_fraction=0.5,
                effective_capacity_watts=1e6,
            )
            for i in range(5)
        ],
    )


class TestDataPlatformClient(unittest.IsolatedAsyncioTestCase):
    @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
    async def test_get_sites(self, client_mock) -> None:
        @dataclasses.dataclass
        class TestCase:
            name: str
            authdata: dict[str, str]
            expected_num_sites: int

        testcases: list[TestCase] = [
            TestCase(
                name="Should return sites when user has access",
                authdata={"sub": "access_user"},
                expected_num_sites=1,
            ),
            TestCase(
                name="Should return no sites when user has no access",
                authdata={"sub": "no_access_user"},
                expected_num_sites=0,
            ),
        ]

        client = Client.from_dp(client_mock)
        for tc in testcases:
            client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)

            with self.subTest(tc.name):
                resp = await client.get_sites(authdata=tc.authdata)
                self.assertEqual(len(resp), tc.expected_num_sites)

    @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
    async def test_get_site_forecast(
        self,
        client_mock,
    ) -> None:
        @dataclasses.dataclass
        class TestCase:
            name: str
            site_uuid: str
            authdata: dict[str, str]
            should_error: bool

        testcases: list[TestCase] = [
            TestCase(
                name="Should return forecast when user has access",
                site_uuid=str(uuid.uuid4()),
                authdata={"sub": "access_user"},
                should_error=False,
            ),
            TestCase(
                name="Should raise HTTPException when user has no access",
                site_uuid=str(uuid.uuid4()),
                authdata={"sub": "no_access_user"},
                should_error=True,
            ),
        ]

        client = Client.from_dp(client_mock)
        for tc in testcases:
            client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)
            client_mock.get_forecast_as_timeseries = AsyncMock(
                side_effect=mock_get_forecast,
            )

            with self.subTest(tc.name):
                if tc.should_error:
                    with self.assertRaises(HTTPException):
                        await client.get_site_forecast(
                            site_uuid=tc.site_uuid,
                            authdata=tc.authdata,
                        )
                else:
                    resp = await client.get_site_forecast(
                        site_uuid=tc.site_uuid,
                        authdata=tc.authdata,
                    )
                    self.assertEqual(len(resp), 5)

    @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
    async def test_get_site_generation(
        self,
        client_mock,
    ) -> None:
        @dataclasses.dataclass
        class TestCase:
            name: str
            site_uuid: str
            authdata: dict[str, str]
            should_error: bool

        testcases: list[TestCase] = [
            TestCase(
                name="Should return generation when user has access",
                site_uuid=str(uuid.uuid4()),
                authdata={"sub": "access_user"},
                should_error=False,
            ),
            TestCase(
                name="Should raise HTTPException when user has no access",
                site_uuid=str(uuid.uuid4()),
                authdata={"sub": "no_access_user"},
                should_error=True,
            ),
        ]

        client = Client.from_dp(client_mock)
        for tc in testcases:
            client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)
            client_mock.get_observations_as_timeseries = AsyncMock(
                side_effect=mock_get_observations,
            )

            with self.subTest(tc.name):
                if tc.should_error:
                    with self.assertRaises(HTTPException):
                        await client.get_site_generation(
                            site_uuid=tc.site_uuid,
                            authdata=tc.authdata,
                        )
                else:
                    resp = await client.get_site_generation(
                        site_uuid=tc.site_uuid,
                        authdata=tc.authdata,
                    )
                    self.assertEqual(len(resp), 5)
