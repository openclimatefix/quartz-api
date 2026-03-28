# import dataclasses
# import datetime as dt
# import unittest
# import uuid
# from unittest.mock import AsyncMock, patch

# from betterproto.lib.google.protobuf import Struct, Value
# from dp_sdk.ocf import dp
# from fastapi import HTTPException

# from quartz_api.internal import models

# from .client import StorageClient

# TEST_TIMESTAMP_UTC = dt.datetime(2024, 2, 1, 12, 0, 0, tzinfo=dt.UTC)


# def mock_list_locations(
#     req: dp.ListLocationsRequest,
#     metadata: object | None = None,
# ) -> dp.ListLocationsResponse:
#     if req.user_oauth_id_filter != "access_user":
#         return dp.ListLocationsResponse(locations=[])

#     match req.location_type_filter:
#         case dp.LocationType.SITE:
#             capacity = 1e3
#         case dp.LocationType.PRIMARY_SUBSTATION:
#             capacity = 1e5
#         case _:
#             capacity = 1e6

#     return dp.ListLocationsResponse(
#         locations=[
#             dp.ListLocationsResponseLocationSummary(
#                 location_name="mock_location",
#                 location_uuid=str(uuid.uuid4()),
#                 energy_source=dp.EnergySource.SOLAR,
#                 effective_capacity_watts=capacity,
#                 location_type=req.location_type_filter,
#                 latlng=dp.LatLng(51.5, -0.1),
#                 metadata=Struct(
#                     fields={
#                         "orientation": Value(number_value=180.0),
#                         "tilt": Value(number_value=30.0),
#                     },
#                 ),
#             ),
#         ],
#     ).to_dict()


# def mock_get_forecast(
#     req: dp.GetForecastAsTimeseriesRequest,
#     metadata: object | None = None,
# ) -> dp.GetForecastAsTimeseriesResponse:
#     return dp.GetForecastAsTimeseriesResponse(
#         values=[
#             dp.GetForecastAsTimeseriesResponseValue(
#                 target_timestamp_utc=TEST_TIMESTAMP_UTC + dt.timedelta(hours=i),
#                 p50_value_fraction=0.5,
#                 effective_capacity_watts=1e6,
#                 initialization_timestamp_utc=TEST_TIMESTAMP_UTC
#                 - dt.timedelta(minutes=req.horizon_mins),
#                 created_timestamp_utc=TEST_TIMESTAMP_UTC
#                 - dt.timedelta(hours=1, minutes=req.horizon_mins),
#                 other_statistics_fractions={"p90": 0.9, "p10": 0.1},
#                 metadata=Struct(fields={}),
#             )
#             for i in range(5)
#         ],
#     )


# def mock_get_observations(
#     _: dp.GetObservationsAsTimeseriesRequest,
#     metadata: object | None = None,
# ) -> dp.GetObservationsAsTimeseriesResponse:
#     return dp.GetObservationsAsTimeseriesResponse(
#         values=[
#             dp.GetObservationsAsTimeseriesResponseValue(
#                 timestamp_utc=TEST_TIMESTAMP_UTC + dt.timedelta(hours=i),
#                 value_fraction=0.5,
#                 effective_capacity_watts=1e6,
#             )
#             for i in range(5)
#         ],
#     )


# def mock_get_latest_forecasts(
#     req: dp.GetLatestForecastsRequest,
#     metadata: object | None = None,
# ) -> dp.GetLatestForecastsResponse:
#     t = req.pivot_timestamp_utc - dt.timedelta(hours=1)
#     forecaster_name = f"mock_forecaster_{t.day}{t.hour}"
#     return dp.GetLatestForecastsResponse(
#         forecasts=[
#             dp.GetLatestForecastsResponseForecast(
#                 initialization_timestamp_utc=t,
#                 created_timestamp_utc=t - dt.timedelta(hours=1),
#                 forecaster=dp.Forecaster(forecaster_name, forecaster_version="1.0"),
#                 location_uuid=req.location_uuid,
#             ),
#         ],
#     )


# class TestDataPlatformClient(unittest.IsolatedAsyncioTestCase):
#     @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
#     async def test_get_locations(self, client_mock: dp.DataPlatformDataServiceStub) -> None:
#         @dataclasses.dataclass
#         class TestCase:
#             name: str
#             authdata: dict[str, str]
#             expected_num_locations: int

#         testcases: list[TestCase] = [
#             TestCase(
#                 name="Should return locations when user has access",
#                 authdata={"sub": "access_user"},
#                 expected_num_locations=1,
#             ),
#             TestCase(
#                 name="Should return no locations when user has no access",
#                 authdata={"sub": "no_access_user"},
#                 expected_num_locations=0,
#             ),
#         ]

#         client = StorageClient.from_dp(client_mock)
#         for tc in testcases:
#             client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)

#             with self.subTest(tc.name):
#                 resp = client.get_locations(authdata=tc.authdata,
#                                                   location_type=models.LocationType.SITE,
#                                                   energy_type=models.EnergyType.SOLAR)
#                 self.assertEqual(len(resp), tc.expected_num_locations)

#     @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
#     async def test_get_site_forecast(self, client_mock: dp.DataPlatformDataServiceStub) -> None:
#         @dataclasses.dataclass
#         class TestCase:
#             name: str
#             site_uuid: uuid.UUID
#             authdata: dict[str, str]
#             should_error: bool

#         testcases: list[TestCase] = [
#             TestCase(
#                 name="Should return forecast when user has access",
#                 site_uuid=uuid.uuid4(),
#                 authdata={"sub": "access_user"},
#                 should_error=False,
#             ),
#             TestCase(
#                 name="Should raise HTTPException when user has no access",
#                 site_uuid=uuid.uuid4(),
#                 authdata={"sub": "no_access_user"},
#                 should_error=True,
#             ),
#         ]

#         client = StorageClient.from_dp(client_mock)
#         for tc in testcases:
#             client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)
#             client_mock.get_forecast_as_timeseries = AsyncMock(side_effect=mock_get_forecast)
#             client_mock.get_latest_forecasts = AsyncMock(side_effect=mock_get_latest_forecasts)

#             with self.subTest(tc.name):
#                 if tc.should_error:
#                     with self.assertRaises(HTTPException):
#                         resp = client.get_predicted_generation(
#                             location_uuid=tc.site_uuid,
#                             authdata=tc.authdata,
#                             location_type=models.LocationType.SITE,
#                             window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
#                             energy_type=models.EnergyType.SOLAR,
#                             window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
#                         )
#                 else:
#                     resp = client.get_predicted_generation(
#                         location_uuid=tc.site_uuid,
#                         authdata=tc.authdata,
#                         window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
#                         window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
#                         location_type=models.LocationType.SITE,
#                         energy_type=models.EnergyType.SOLAR,
#                     )
#                     self.assertEqual(len(resp), 5)

#     @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
#     async def test_get_site_generation(
#         self,
#         client_mock: dp.DataPlatformDataServiceStub,
#     ) -> None:
#         @dataclasses.dataclass
#         class TestCase:
#             name: str
#             site_uuid: uuid.UUID
#             authdata: dict[str, str]
#             should_error: bool

#         testcases: list[TestCase] = [
#             TestCase(
#                 name="Should return generation when user has access",
#                 site_uuid=uuid.uuid4(),
#                 authdata={"sub": "access_user"},
#                 should_error=False,
#             ),
#             TestCase(
#                 name="Should raise HTTPException when user has no access",
#                 site_uuid=uuid.uuid4(),
#                 authdata={"sub": "no_access_user"},
#                 should_error=True,
#             ),
#         ]

#         client = StorageClient.from_dp(client_mock)
#         for tc in testcases:
#             client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)
#             client_mock.get_observations_as_timeseries = AsyncMock(
#                 side_effect=mock_get_observations,
#             )

#             with self.subTest(tc.name):
#                 if tc.should_error:
#                     with self.assertRaises(HTTPException):
#                         client.get_actual_generation(
#                             location_uuid=tc.site_uuid,
#                             authdata=tc.authdata,
#                             window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
#                             window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
#                             location_type=models.LocationType.SITE,
#                             energy_type=models.EnergyType.SOLAR,
#                             observer_name="test_observer",
#                         )
#                 else:
#                     resp = client.get_actual_generation(
#                         location_uuid=tc.site_uuid,
#                         authdata=tc.authdata,
#                         window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
#                         window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
#                         location_type=models.LocationType.SITE,
#                         energy_type=models.EnergyType.SOLAR,
#                         observer_name="test_observer",
#                     )
#                     self.assertEqual(len(resp), 5)

#     @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
#     async def test_get_substations(
#         self,
#         client_mock: dp.DataPlatformDataServiceStub,
#     ) -> None:
#         @dataclasses.dataclass
#         class TestCase:
#             name: str
#             authdata: dict[str, str]
#             expected_num_substations: int

#         testcases: list[TestCase] = [
#             TestCase(
#                 name="Should return substations when user has access",
#                 authdata={"sub": "access_user"},
#                 expected_num_substations=1,
#             ),
#             TestCase(
#                 name="Should return no substations when user has no access",
#                 authdata={"sub": "no_access_user"},
#                 expected_num_substations=0,
#             ),
#         ]

#         client = StorageClient.from_dp(client_mock)
#         for tc in testcases:
#             client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)

#             with self.subTest(tc.name):
#                 resp = client.get_locations(authdata=tc.authdata,
#                                                   location_type=models.LocationType.SUBSTATION,
#                                                   energy_type=models.EnergyType.SOLAR)
#                 self.assertEqual(len(resp), tc.expected_num_substations)

#     @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
#     async def test_get_substation(
#         self,
#         client_mock: dp.DataPlatformDataServiceStub,
#     ) -> None:
#         @dataclasses.dataclass
#         class TestCase:
#             name: str
#             location_uuid: uuid.UUID
#             authdata: dict[str, str]
#             should_error: bool
#             number_of_locations: int

#         testcases: list[TestCase] = [
#             TestCase(
#                 name="Should return substation when user has access",
#                 location_uuid=uuid.uuid4(),
#                 authdata={"sub": "access_user"},
#                 should_error=False,
#                 number_of_locations=1,
#             ),
#             TestCase(
#                 name="Should raise HTTPException when user has no access",
#                 location_uuid=uuid.uuid4(),
#                 authdata={"sub": "no_access_user"},
#                 should_error=False,
#                 number_of_locations=0,
#             ),
#         ]

#         client = StorageClient.from_dp(client_mock)
#         for tc in testcases:
#             client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)

#             with self.subTest(tc.name):
#                 if tc.should_error:
#                     with self.assertRaises(HTTPException):
#                         client.get_locations(
#                             location_uuid=tc.location_uuid,
#                             authdata=tc.authdata,
#                             energy_type=models.EnergyType.SOLAR,
#                             location_type=models.LocationType.SUBSTATION,
#                         )
#                 else:
#                     resp = client.get_locations(
#                         location_uuid=tc.location_uuid,
#                         authdata=tc.authdata,
#                         energy_type=models.EnergyType.SOLAR,
#                         location_type=models.LocationType.SUBSTATION,
#                     )
#                     self.assertIsNotNone(resp)
#                     self.assertEqual(len(resp), tc.number_of_locations)

#     @patch("dp_sdk.ocf.dp.DataPlatformDataServiceStub")
#     async def test_get_substation_forecast(
#         self,
#         client_mock: dp.DataPlatformDataServiceStub,
#     ) -> None:
#         @dataclasses.dataclass
#         class TestCase:
#             name: str
#             substation_uuid: uuid.UUID
#             authdata: dict[str, str]
#             expected_values: list[float]
#             should_error: bool

#         testcases: list[TestCase] = [
#             TestCase(
#                 name="Should return GSP-scaled forecast when user has access",
#                 substation_uuid=uuid.uuid4(),
#                 authdata={"sub": "access_user"},
#                 # The forecast returns 5e5 watts for every value, and the substation's
#                 # effective capacity is 1e5 watts (10% of the GSP's 1e6 watts), so
#                 # the scaled values should be 0.1*5e5W = 50kW for each entry.
#                 expected_values=[50] * 5,
#                 should_error=False,
#             ),
#             TestCase(
#                 name="Should raise HTTPException when user has no access",
#                 substation_uuid=uuid.uuid4(),
#                 authdata={"sub": "no_access_user"},
#                 expected_values=[],
#                 should_error=True,
#             ),
#         ]

#         client = StorageClient.from_dp(client_mock)
#         for tc in testcases:
#             client_mock.list_locations = AsyncMock(side_effect=mock_list_locations)
#             client_mock.get_forecast_as_timeseries = AsyncMock(side_effect=mock_get_forecast)
#             client_mock.get_latest_forecasts = AsyncMock(side_effect=mock_get_latest_forecasts)

#             with self.subTest(tc.name):
#                 if tc.should_error:
#                     with self.assertRaises(HTTPException):
#                         resp = client.get_predicted_generation(
#                             location_uuid=tc.substation_uuid,
#                             authdata=tc.authdata,
#                             window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
#                             window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
#                             energy_type=models.EnergyType.SOLAR,
#                             location_type=models.LocationType.SUBSTATION,
#                         )
#                 else:
#                     resp = client.get_predicted_generation(
#                         location_uuid=tc.substation_uuid,
#                         authdata=tc.authdata,
#                         window_start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
#                         window_end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
#                         energy_type=models.EnergyType.SOLAR,
#                         location_type=models.LocationType.SUBSTATION,
#                     )
#                     actual_values = [v.power_kilowatts for v in resp]
#                     self.assertListEqual(actual_values, tc.expected_values)


# # TODO add test for get_latest_forecasts
