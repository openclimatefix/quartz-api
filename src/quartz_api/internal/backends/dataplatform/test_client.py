import dataclasses
import datetime as dt
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from google.protobuf.struct_pb2 import Struct
from ocf.dp.dp import common_pb2
from ocf.dp.dp_data import messages_pb2, service_pb2_grpc

from quartz_api.internal import models

from .client import StorageClient

TEST_TIMESTAMP_UTC = dt.datetime(2024, 2, 1, 12, 0, 0, tzinfo=dt.UTC)


def mock_list_locations(
    req: messages_pb2.ListLocationsRequest,
    metadata: object | None = None,
) -> messages_pb2.ListLocationsResponse:
    if req.user_oauth_id_filter != "access_user":
        return messages_pb2.ListLocationsResponse(locations=[])

    match req.location_type_filter:
        case common_pb2.LOCATION_TYPE_SITE:
            capacity = 1e3
        case common_pb2.LocationType.LOCATION_TYPE_PRIMARY_SUBSTATION:
            capacity = 1e5
        case _:
            capacity = 1e6

    metadata = Struct()
    metadata.update({"orientation": 180.0, "tilt": 30.0})

    return messages_pb2.ListLocationsResponse(
        locations=[
            messages_pb2.ListLocationsResponse.LocationSummary(
                location_name="mock_location",
                location_uuid=str(uuid.uuid4()),
                energy_source=common_pb2.EnergySource.ENERGY_SOURCE_SOLAR,
                effective_capacity_watts=int(capacity),
                location_type=req.location_type_filter,
                latlng=common_pb2.LatLng(latitude=51.5, longitude=-0.1),
                metadata=metadata,
            ),
        ],
    )


def mock_get_forecast(
    req: messages_pb2.GetForecastAsTimeseriesRequest,
    metadata: object | None = None,  # noqa: ARG001
) -> messages_pb2.GetForecastAsTimeseriesResponse:
    return messages_pb2.GetForecastAsTimeseriesResponse(
        location_uuid=req.location_uuid,
        location_name="mock_location",
        values=[
            messages_pb2.GetForecastAsTimeseriesResponse.Value(
                target_timestamp_utc=TEST_TIMESTAMP_UTC + dt.timedelta(hours=i),
                p50_value_fraction=0.5,
                effective_capacity_watts=int(1e6),
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
    req: messages_pb2.GetObservationsAsTimeseriesRequest,
    metadata: object | None = None,  # noqa: ARG001
) -> messages_pb2.GetObservationsAsTimeseriesResponse:
    return messages_pb2.GetObservationsAsTimeseriesResponse(
        location_uuid=req.location_uuid,
        values=[
            messages_pb2.GetObservationsAsTimeseriesResponse.Value(
                timestamp_utc=TEST_TIMESTAMP_UTC + dt.timedelta(hours=i),
                value_fraction=0.5,
                effective_capacity_watts=int(1e6),
            )
            for i in range(5)
        ],
    )


def mock_get_latest_forecasts(
    req: messages_pb2.GetLatestForecastsRequest,
    metadata: object | None = None,  # noqa: ARG001
) -> messages_pb2.GetLatestForecastsResponse:
    t = req.pivot_timestamp_utc - dt.timedelta(hours=1)
    forecaster_name = f"mock_forecaster_{t.day}{t.hour}"
    return messages_pb2.GetLatestForecastsResponse(
        forecasts=[
            messages_pb2.GetLatestForecastsResponse.Forecast(
                initialization_timestamp_utc=t,
                created_timestamp_utc=t - dt.timedelta(hours=1),
                forecaster=messages_pb2.Forecaster(
                    forecaster_name=forecaster_name,
                    forecaster_version="1.0",
                ),
                location_uuid=req.location_uuid,
            ),
        ],
    )

def mock_get_forecast_at_timestamp(
    req: messages_pb2.GetForecastAtTimestampRequest,
    metadata: object | None = None,  # noqa: ARG001
) -> messages_pb2.GetForecastAtTimestampResponse:
    return messages_pb2.GetForecastAtTimestampResponse(
        timestamp_utc=req.timestamp_utc,
        values=[
            messages_pb2.GetForecastAtTimestampResponse.Value(
                location_uuid=uuid,
                value_fraction=0.8,
                effective_capacity_watts=int(1e6),
                created_timestamp_utc=TEST_TIMESTAMP_UTC - dt.timedelta(hours=1),
                initialization_timestamp_utc=TEST_TIMESTAMP_UTC - dt.timedelta(hours=2),
                metadata=Struct(fields={}),
            )
            for uuid in req.location_uuids
        ],
    )


def mock_get_observations_at_timestamp(
    req: messages_pb2.GetObservationsAtTimestampRequest,
    metadata: object | None = None,  # noqa: ARG001
) -> messages_pb2.GetObservationsAtTimestampResponse:
    return messages_pb2.GetObservationsAtTimestampResponse(
        timestamp_utc=req.timestamp_utc,
        values=[
            messages_pb2.GetObservationsAtTimestampResponse.Value(
                location_uuid=uuid,
                value_fraction=0.5,
                effective_capacity_watts=int(1e6),
            )
            for uuid in req.location_uuids
        ],
    )


class TestDataPlatformClient(unittest.IsolatedAsyncioTestCase):
    @patch("ocf.dp.dp_data.service_pb2_grpc.DataPlatformDataServiceStub")
    async def test_get_locations(
        self,
        client_mock: service_pb2_grpc.DataPlatformDataServiceStub,
    ) -> None:
        @dataclasses.dataclass
        class TestCase:
            name: str
            authdata: dict[str, str]
            expected_num_locations: int

        testcases: list[TestCase] = [
            TestCase(
                name="Should return locations when user has access",
                authdata={"sub": "access_user"},
                expected_num_locations=1,
            ),
            TestCase(
                name="Should return no locations when user has no access",
                authdata={"sub": "no_access_user"},
                expected_num_locations=0,
            ),
        ]

        client = StorageClient.from_dp(client_mock)
        for tc in testcases:
            client_mock.ListLocations = AsyncMock(side_effect=mock_list_locations)

            with self.subTest(tc.name):
                resp = await client.get_locations(authdata=tc.authdata,
                                                  location_type=models.LocationType.SITE,
                                                  energy_type=models.EnergyType.SOLAR)
                self.assertEqual(len(resp), tc.expected_num_locations)

    @patch("ocf.dp.dp_data.service_pb2_grpc.DataPlatformDataServiceStub")
    async def test_get_site_forecast(
        self,
        client_mock: service_pb2_grpc.DataPlatformDataServiceStub,
    ) -> None:
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

        client = StorageClient.from_dp(client_mock)
        for tc in testcases:
            client_mock.ListLocations = AsyncMock(side_effect=mock_list_locations)
            client_mock.GetForecastAsTimeseries = AsyncMock(side_effect=mock_get_forecast)
            client_mock.GetLatestForecasts = AsyncMock(side_effect=mock_get_latest_forecasts)

            with self.subTest(tc.name):
                if tc.should_error:
                    with self.assertRaises(HTTPException):
                        resp = await client.get_predicted_generation(
                            location_uuid=tc.site_uuid,
                            authdata=tc.authdata,
                            location_type=models.LocationType.SITE,
                            window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                            energy_type=models.EnergyType.SOLAR,
                            window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                        )
                else:
                    resp = await client.get_predicted_generation(
                        location_uuid=tc.site_uuid,
                        authdata=tc.authdata,
                        window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                        window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                        location_type=models.LocationType.SITE,
                        energy_type=models.EnergyType.SOLAR,
                    )
                    self.assertEqual(len(resp), 5)

    @patch("ocf.dp.dp_data.service_pb2_grpc.DataPlatformDataServiceStub")
    async def test_get_site_generation(
        self,
        client_mock: service_pb2_grpc.DataPlatformDataServiceStub,
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

        client = StorageClient.from_dp(client_mock)
        for tc in testcases:
            client_mock.ListLocations = AsyncMock(side_effect=mock_list_locations)
            client_mock.GetObservationsAsTimeseries = AsyncMock(
                side_effect=mock_get_observations,
            )

            with self.subTest(tc.name):
                if tc.should_error:
                    with self.assertRaises(HTTPException):
                        await client.get_actual_generation(
                            location_uuid=tc.site_uuid,
                            authdata=tc.authdata,
                            window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                            window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                            location_type=models.LocationType.SITE,
                            energy_type=models.EnergyType.SOLAR,
                            observer_name="test_observer",
                        )
                else:
                    resp = await client.get_actual_generation(
                        location_uuid=tc.site_uuid,
                        authdata=tc.authdata,
                        window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                        window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                        location_type=models.LocationType.SITE,
                        energy_type=models.EnergyType.SOLAR,
                        observer_name="test_observer",
                    )
                    self.assertEqual(len(resp), 5)

    @patch("ocf.dp.dp_data.service_pb2_grpc.DataPlatformDataServiceStub")
    async def test_get_substations(
        self,
        client_mock: service_pb2_grpc.DataPlatformDataServiceStub,
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

        client = StorageClient.from_dp(client_mock)
        for tc in testcases:
            client_mock.ListLocations = AsyncMock(side_effect=mock_list_locations)

            with self.subTest(tc.name):
                resp = await client.get_locations(authdata=tc.authdata,
                                                  location_type=models.LocationType.SUBSTATION,
                                                  energy_type=models.EnergyType.SOLAR)
                self.assertEqual(len(resp), tc.expected_num_substations)

    @patch("ocf.dp.dp_data.service_pb2_grpc.DataPlatformDataServiceStub")
    async def test_get_substation(
        self,
        client_mock: service_pb2_grpc.DataPlatformDataServiceStub,
    ) -> None:
        @dataclasses.dataclass
        class TestCase:
            name: str
            location_uuid: uuid.UUID
            authdata: dict[str, str]
            should_error: bool
            number_of_locations: int

        testcases: list[TestCase] = [
            TestCase(
                name="Should return substation when user has access",
                location_uuid=uuid.uuid4(),
                authdata={"sub": "access_user"},
                should_error=False,
                number_of_locations=1,
            ),
            TestCase(
                name="Should raise HTTPException when user has no access",
                location_uuid=uuid.uuid4(),
                authdata={"sub": "no_access_user"},
                should_error=False,
                number_of_locations=0,
            ),
        ]

        client = StorageClient.from_dp(client_mock)
        for tc in testcases:
            client_mock.ListLocations = AsyncMock(side_effect=mock_list_locations)

            with self.subTest(tc.name):
                if tc.should_error:
                    with self.assertRaises(HTTPException):
                        await client.get_locations(
                            location_uuid=tc.location_uuid,
                            authdata=tc.authdata,
                            energy_type=models.EnergyType.SOLAR,
                            location_type=models.LocationType.SUBSTATION,
                        )
                else:
                    resp = await client.get_locations(
                        location_uuid=tc.location_uuid,
                        authdata=tc.authdata,
                        energy_type=models.EnergyType.SOLAR,
                        location_type=models.LocationType.SUBSTATION,
                    )
                    self.assertIsNotNone(resp)
                    self.assertEqual(len(resp), tc.number_of_locations)

    @patch("ocf.dp.dp_data.service_pb2_grpc.DataPlatformDataServiceStub")
    async def test_get_substation_forecast(
        self,
        client_mock: service_pb2_grpc.DataPlatformDataServiceStub,
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

        client = StorageClient.from_dp(client_mock)
        for tc in testcases:
            client_mock.ListLocations = AsyncMock(side_effect=mock_list_locations)
            client_mock.GetForecastAsTimeseries = AsyncMock(side_effect=mock_get_forecast)
            client_mock.GetLatestForecasts = AsyncMock(side_effect=mock_get_latest_forecasts)

            with self.subTest(tc.name):
                if tc.should_error:
                    with self.assertRaises(HTTPException):
                        resp = await client.get_predicted_generation(
                            location_uuid=tc.substation_uuid,
                            authdata=tc.authdata,
                            window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                            window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                            energy_type=models.EnergyType.SOLAR,
                            location_type=models.LocationType.SUBSTATION,
                        )
                else:
                    resp = await client.get_predicted_generation(
                        location_uuid=tc.substation_uuid,
                        authdata=tc.authdata,
                        window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                        window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                        energy_type=models.EnergyType.SOLAR,
                        location_type=models.LocationType.SUBSTATION,
                    )
                    actual_values = [v.power_kilowatts for v in resp]
                    self.assertListEqual(actual_values, tc.expected_values)

    @patch("ocf.dp.dp_data.service_pb2_grpc.DataPlatformDataServiceStub")
    async def test_get_predicted_generation_snapshot(
        self,
        client_mock: service_pb2_grpc.DataPlatformDataServiceStub,
    ) -> None:
        client = StorageClient.from_dp(client_mock)

        client_mock.ListForecasters = AsyncMock(side_effect=mock_list_locations)
        client_mock.GetForecastAtTimestamp = AsyncMock(side_effect=mock_get_forecast_at_timestamp)
        client_mock.ListForecasters = AsyncMock(
            return_value=messages_pb2.ListForecastersResponse(
                forecasters=[messages_pb2.Forecaster(
                    forecaster_name="blend",
                    forecaster_version="1.3.0",
                )],
            ),
        )

        test_uuids = [uuid.uuid4(), uuid.uuid4()]

        with self.assertRaises(ValueError):
            await client.get_predicted_generation_snapshot(
                location_uuids=test_uuids,
                snapshot_timestamp_utc=TEST_TIMESTAMP_UTC,
                energy_type=models.EnergyType.SOLAR,
                authdata={"sub": "access_user"},
                forecaster_name=None,
            )

        resp = await client.get_predicted_generation_snapshot(
            location_uuids=test_uuids,
            snapshot_timestamp_utc=TEST_TIMESTAMP_UTC,
            energy_type=models.EnergyType.SOLAR,
            authdata={"sub": "access_user"},
            forecaster_name="blend",
            # forecaster_version is omitted to force ListForecasters call
        )

        self.assertEqual(len(resp), 2)
        client_mock.ListForecasters.assert_called_once()
        client_mock.GetForecastAtTimestamp.assert_called_once()
        self.assertEqual(resp[0].power_kilowatts, 800.0)

    @patch("ocf.dp.dp_data.service_pb2_grpc.DataPlatformDataServiceStub")
    async def test_get_actual_generation_snapshot(
        self,
        client_mock: service_pb2_grpc.DataPlatformDataServiceStub,
    ) -> None:
        client = StorageClient.from_dp(client_mock)
        client_mock.GetObservationsAtTimestamp = AsyncMock(
            side_effect=mock_get_observations_at_timestamp,
        )

        test_uuids = [uuid.uuid4(), uuid.uuid4()]

        with self.assertRaises(ValueError):
            await client.get_actual_generation_snapshot(
                location_uuids=test_uuids,
                snapshot_timestamp_utc=TEST_TIMESTAMP_UTC,
                energy_type=models.EnergyType.SOLAR,
                authdata={"sub": "access_user"},
                observer_name=None,
            )

        resp = await client.get_actual_generation_snapshot(
            location_uuids=test_uuids,
            snapshot_timestamp_utc=TEST_TIMESTAMP_UTC,
            energy_type=models.EnergyType.SOLAR,
            authdata={"sub": "access_user"},
            observer_name="test_observer",
        )

        self.assertEqual(len(resp), 2)
        client_mock.GetObservationsAtTimestamp.assert_called_once()

        self.assertEqual(resp[0].power_kilowatts, 500.0)

    @patch("ocf.dp.dp_data.service_pb2_grpc.DataPlatformDataServiceStub")
    async def test_put_actual_generation(
        self,
        client_mock: service_pb2_grpc.DataPlatformDataServiceStub,
    ) -> None:
        site_uuid = uuid.uuid4()
        gen_values = [
            models.ActualGenerationValue(
                power_kilowatts=1.5,
                valid_timestamp=TEST_TIMESTAMP_UTC,
                location_uuid=site_uuid,
                capacity_kilowatts=0,
                observer_name="ruvnl",
            ),
        ]
        client = StorageClient.from_dp(client_mock)

        # When the user has access, observations are written as absolute watts.
        client_mock.ListLocations = AsyncMock(side_effect=mock_list_locations)
        client_mock.CreateObservations = AsyncMock(
            return_value=messages_pb2.CreateObservationsResponse(),
        )
        await client.put_actual_generation(
            generation_values=gen_values,
            location_uuid=site_uuid,
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.SITE,
            authdata={"sub": "access_user"},
        )
        client_mock.CreateObservations.assert_called_once()
        sent = client_mock.CreateObservations.call_args.args[0]
        self.assertEqual(sent.observer_name, "ruvnl")
        self.assertEqual(sent.values[0].value_watts, 1500)

        # When the user has no access, the write is rejected.
        client_mock.ListLocations = AsyncMock(side_effect=mock_list_locations)
        with self.assertRaises(HTTPException):
            await client.put_actual_generation(
                generation_values=gen_values,
                location_uuid=site_uuid,
                energy_type=models.EnergyType.SOLAR,
                location_type=models.LocationType.SITE,
                authdata={"sub": "no_access_user"},
            )

    @patch("ocf.dp.dp_data.service_pb2_grpc.DataPlatformDataServiceStub")
    async def test_put_location(
        self,
        client_mock: service_pb2_grpc.DataPlatformDataServiceStub,
    ) -> None:
        def mock_update_location(
            req: messages_pb2.UpdateLocationRequest,
            metadata: object | None = None,  # noqa: ARG001
        ) -> messages_pb2.UpdateLocationResponse:
            return messages_pb2.UpdateLocationResponse(
                location_uuid=req.location_uuid,
                location_name=req.new_location_name or "mock_location",
                effective_capacity_watts=req.new_effective_capacity_watts,
            )

        site_uuid = uuid.uuid4()
        loc = models.Location(
            uuid=site_uuid,
            name="updated_site_name",
            latitude=0.0,
            longitude=0.0,
            capacity_kilowatts=5.0,
            metadata={"tilt": 35.0},
        )
        client = StorageClient.from_dp(client_mock)

        client_mock.ListLocations = AsyncMock(side_effect=mock_list_locations)
        client_mock.UpdateLocation = AsyncMock(side_effect=mock_update_location)

        resp = await client.put_location(
            location=loc,
            location_type=models.LocationType.SITE,
            energy_type=models.EnergyType.SOLAR,
            authdata={"sub": "access_user"},
        )
        # Capacity round-trips kW -> watts -> kW, and lat/lng come from the fetched location.
        self.assertEqual(resp.capacity_kilowatts, 5.0)
        self.assertEqual(resp.latitude, 51.5)

        sent = client_mock.UpdateLocation.call_args.args[0]
        self.assertEqual(sent.new_effective_capacity_watts, 5000)
        # Metadata is merged: existing orientation preserved, tilt overridden by the new value.
        self.assertEqual(dict(sent.new_metadata)["orientation"], 180.0)
        self.assertEqual(dict(sent.new_metadata)["tilt"], 35.0)

        # Updating a location the user cannot see raises a 404.
        client_mock.ListLocations = AsyncMock(side_effect=mock_list_locations)
        with self.assertRaises(HTTPException):
            await client.put_location(
                location=loc,
                location_type=models.LocationType.SITE,
                energy_type=models.EnergyType.SOLAR,
                authdata={"sub": "no_access_user"},
            )

