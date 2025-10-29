import logging

import pandas as pd
from fastapi import HTTPException
import pytest

from quartz_api.internal import PredictedPower, ActualPower, SiteProperties

from pvsite_datamodel.sqlmodels import APIRequestSQL

from .client import Client
from .conftest import forecast_values
from ...models import ForecastHorizon
from ...service.csv import format_csv_and_created_time

log = logging.getLogger(__name__)

# TODO add list of test that are here


@pytest.fixture()
def client(engine, db_session):
    """Hooks Client into pytest db_session fixture"""
    client = Client(database_url=str(engine.url))
    client.session = db_session

    return client

# Skip for now
@pytest.mark.skip(reason="Not finished yet")
class TestCsvExport:
    def test_format_csv_and_created_time(self, client, forecast_values_wind) -> None:
        """Test the format_csv_and_created_time function."""
        forecast_values_wind = client.get_predicted_wind_power_production_for_location(
            location="testID"
        )
        assert forecast_values_wind is not None
        assert len(forecast_values_wind) > 0
        assert isinstance(forecast_values_wind[0], PredictedPower)

        result = format_csv_and_created_time(
            forecast_values_wind,
            ForecastHorizon.latest,
        )
        assert isinstance(result, tuple)
        assert isinstance(result[0], pd.DataFrame)
        assert isinstance(result[1], pd.Timestamp)
        logging.info(f"CSV created at: {result[1]}")
        logging.info(f"CSV content: {result[0].head()}")
        # Check the shape of the DataFrame
        # The shape should match the number of forecast values
        # and the number of columns in the DataFrame
        # The DataFrame should have 3 columns: Date [IST], Time, PowerMW
        assert result[0].shape[1] == 3
        # Check the first row of the DataFrame
        # The date of the first row should be the nearest rounded 15min from now
        rounded_15_min = pd.Timestamp.now(tz="Asia/Kolkata").round("15min")
        assert result[0].iloc[0]["Time"] == rounded_15_min.strftime("%H:%M")
        # Check the number of rows in the DataFrame
        # For the latest forecast, it should be the number of
        # forecast values after now
        forecast_values_from_now = [
            value for value in forecast_values_wind if value.Time >= rounded_15_min
        ]
        assert result[0].shape[0] == len(forecast_values_from_now)
        # Check the column names
        assert list(result[0].columns) == ["Date [IST]", "Time", "PowerMW"]
        # Check the data types of the columns
        assert result[0]["Date [IST]"].dtype == "datetime64[ns, Asia/Kolkata]"
        assert result[0]["Time"].dtype == "object"
        assert result[0]["PowerMW"].dtype == "float64"
