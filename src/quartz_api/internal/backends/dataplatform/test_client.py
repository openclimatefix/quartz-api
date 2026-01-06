import dataclasses
import datetime as dt
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from betterproto.lib.google.protobuf import Struct, Value
from dp_sdk.ocf import dp
from fastapi import HTTPException

from .client import Client

TEST_TIMESTAMP_UTC = dt.datetime(2024, 2, 1, 12, 0, 0, tzinfo=dt.UTC)


def mock_list_locations(
    req: dp.ListLocationsRequest,
    metadata: object | None = None, # noqa: ARG001
) -> dp.ListLocationsResponse:
    if req.user_oauth_id_filter != "access_user":
        return dp.ListLocationsResponse(locations=[])

    match req.location_type_filter:
        case dp.LocationType.SITE:
            capacity = 1e3
        case dp.LocationType.PRIMARY_SUBSTATION:
            capacity = 1e5
        case _:
            capacity = 1e6

    return dp.ListLocationsResponse(
        locations=[
            dp.ListLocationsResponseLocationSummary(
                location_name="mock_location",
                location_uuid=str(uuid.uuid4()),
                energy_source=dp.EnergySource.SOLAR,
                effective_capacity_watts=capacity,
                location_type=req.location_type_filter,
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


def mock_get_forecast(
    req: dp.GetForecastAsTimeseriesRequest,
    metadata: object | None = None, # noqa: ARG001
) -> dp.GetForecastAsTimeseriesResponse:
    return dp.GetForecastAsTimeseriesResponse(
        values=[
            dp.GetForecastAsTimeseriesResponseValue(
                target_timestamp_utc=TEST_TIMESTAMP_UTC + dt.timedelta(hours=i),
                p50_value_fraction=0.5,
                effective_capacity_watts=1e6,
                initialization_timestamp_utc=TEST_TIMESTAMP_UTC
                - dt.timedelta(minutes=req.horizon_mins),
                created_timestamp_utc=TEST_TIMESTAMP_UTC
                - dt.timedelta(hours=1, minutes=req.horizon_mins),
                other_statistics_fractions={"p90": 0.9, "p10": 0.1},
                metadata=Struct(fields={}),
            )
            for i in range(5)
        ],
    )


def mock_get_observations(
    _: dp.GetObservationsAsTimeseriesRequest,
    metadata: object | None = None, # noqa: ARG001
) -> dp.GetObservationsAsTimeseriesResponse:
    return dp.GetObservationsAsTimeseriesResponse(
        values=[
            dp.GetObservationsAsTimeseriesResponseValue(
                timestamp_utc=TEST_TIMESTAMP_UTC + dt.timedelta(hours=i),
                value_fraction=0.5,
                effective_capacity_watts=1e6,
            )
            for i in range(5)
        ],
    )


def mock_get_latest_forecasts(
    req: dp.GetLatestForecastsRequest,
    metadata: object | None = None, # noqa: ARG001
) -> dp.GetLatestForecastsResponse:
    t = req.pivot_timestamp_utc - dt.timedelta(hours=1)
    forecaster_name = f"mock_forecaster_{t.day}{t.hour}"
    return dp.GetLatestForecastsResponse(
        forecasts=[
            dp.GetLatestForecastsResponseForecast(
                initialization_timestamp_utc=t,
                created_timestamp_utc=t - dt.timedelta(hours=1),
                forecaster=dp.Forecaster(forecaster_name, forecaster_version="1.0"),
                location_uuid=req.location_uuid,
            ),
        ],
    )


class TestDataPlatformClient(unittest.IsolatedAsyncioTestCase):
    @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
    async def test_get_sites(self, client_mock: dp.DataPlatformDataServiceStub) -> None:
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
    async def test_get_site_forecast(self, client_mock: dp.DataPlatformDataServiceStub) -> None:
        @dataclasses.dataclass
        class TestCase:
            name: str
            site_uuid: uuid.UUID
            authdata: dict[str, str]
            should_error: bool

        testcases: list[TestCase] = [
            TestCase(
                name="Should return forecast when user has access",
                site_uuid=uuid.uuid4(),
                authdata={"sub": "access_user"},
                should_error=False,
            ),
            TestCase(
                name="Should raise HTTPException when user has no access",
                site_uuid=uuid.uuid4(),
                authdata={"sub": "no_access_user"},
                should_error=True,
            ),
        ]

        client = Client.from_dp(client_mock)
        for tc in testcases:
            client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)
            client_mock.get_forecast_as_timeseries = AsyncMock(side_effect=mock_get_forecast)
            client_mock.get_latest_forecasts = AsyncMock(side_effect=mock_get_latest_forecasts)

            with self.subTest(tc.name):
                if tc.should_error:
                    with self.assertRaises(HTTPException):
                        resp = await client.get_site_forecast(
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
        client_mock: dp.DataPlatformDataServiceStub,
    ) -> None:
        @dataclasses.dataclass
        class TestCase:
            name: str
            site_uuid: uuid.UUID
            authdata: dict[str, str]
            should_error: bool

        testcases: list[TestCase] = [
            TestCase(
                name="Should return generation when user has access",
                site_uuid=uuid.uuid4(),
                authdata={"sub": "access_user"},
                should_error=False,
            ),
            TestCase(
                name="Should raise HTTPException when user has no access",
                site_uuid=uuid.uuid4(),
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

    @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
    async def test_get_substations(
        self,
        client_mock: dp.DataPlatformDataServiceStub,
    ) -> None:
        @dataclasses.dataclass
        class TestCase:
            name: str
            authdata: dict[str, str]
            expected_num_substations: int

        testcases: list[TestCase] = [
            TestCase(
                name="Should return substations when user has access",
                authdata={"sub": "access_user"},
                expected_num_substations=1,
            ),
            TestCase(
                name="Should return no substations when user has no access",
                authdata={"sub": "no_access_user"},
                expected_num_substations=0,
            ),
        ]

        client = Client.from_dp(client_mock)
        for tc in testcases:
            client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)

            with self.subTest(tc.name):
                resp = await client.get_substations(authdata=tc.authdata)
                self.assertEqual(len(resp), tc.expected_num_substations)

    @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
    async def test_get_substation(
        self,
        client_mock: dp.DataPlatformDataServiceStub,
    ) -> None:
        @dataclasses.dataclass
        class TestCase:
            name: str
            location_uuid: uuid.UUID
            authdata: dict[str, str]
            should_error: bool

        testcases: list[TestCase] = [
            TestCase(
                name="Should return substation when user has access",
                location_uuid=uuid.uuid4(),
                authdata={"sub": "access_user"},
                should_error=False,
            ),
            TestCase(
                name="Should raise HTTPException when user has no access",
                location_uuid=uuid.uuid4(),
                authdata={"sub": "no_access_user"},
                should_error=True,
            ),
        ]

        client = Client.from_dp(client_mock)
        for tc in testcases:
            client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)

            with self.subTest(tc.name):
                if tc.should_error:
                    with self.assertRaises(HTTPException):
                        await client.get_substation(
                            location_uuid=tc.location_uuid,
                            authdata=tc.authdata,
                        )
                else:
                    resp = await client.get_substation(
                        location_uuid=tc.location_uuid,
                        authdata=tc.authdata,
                    )
                    self.assertIsNotNone(resp)

    @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
    async def test_get_substation_forecast(
        self,
        client_mock: dp.DataPlatformDataServiceStub,
    ) -> None:
        @dataclasses.dataclass
        class TestCase:
            name: str
            substation_uuid: uuid.UUID
            authdata: dict[str, str]
            expected_values: list[float]
            should_error: bool

        testcases: list[TestCase] = [
            TestCase(
                name="Should return GSP-scaled forecast when user has access",
                substation_uuid=uuid.uuid4(),
                authdata={"sub": "access_user"},
                # The forecast returns 5e5 watts for every value, and the substation's
                # effective capacity is 1e5 watts (10% of the GSP's 1e6 watts), so
                # the scaled values should be 0.1*5e5W = 50kW for each entry.
                expected_values=[50] * 5,
                should_error=False,
            ),
            TestCase(
                name="Should raise HTTPException when user has no access",
                substation_uuid=uuid.uuid4(),
                authdata={"sub": "no_access_user"},
                expected_values=[],
                should_error=True,
            ),
        ]

        client = Client.from_dp(client_mock)
        for tc in testcases:
            client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)
            client_mock.get_forecast_as_timeseries = AsyncMock(side_effect=mock_get_forecast)
            client_mock.get_latest_forecasts = AsyncMock(side_effect=mock_get_latest_forecasts)

            with self.subTest(tc.name):
                if tc.should_error:
                    with self.assertRaises(HTTPException):
                        resp = await client.get_substation_forecast(
                            location_uuid=tc.substation_uuid,
                            authdata=tc.authdata,
                        )
                else:
                    resp = await client.get_substation_forecast(
                        location_uuid=tc.substation_uuid,
                        authdata=tc.authdata,
                    )
                    actual_values = [v.PowerKW for v in resp]
                    self.assertListEqual(actual_values, tc.expected_values)
