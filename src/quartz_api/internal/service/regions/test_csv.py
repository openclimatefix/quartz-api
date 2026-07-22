import datetime as dt
import logging
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from quartz_api.internal import models

from ._csv import format_csv_and_created_time

log = logging.getLogger(__name__)


class TestCsvExport:
    def test_format_csv_and_created_time(self) -> None:
        """Test the format_csv_and_created_time function."""
        location_uuid = uuid4()

        start_timestamp = dt.datetime(2021, 10, 1, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
        created_timestamp = start_timestamp - dt.timedelta(hours=1)
        now = dt.datetime(2021, 10, 1, 5, 30, 0, tzinfo=dt.UTC)

        times = [
            t.to_pydatetime()
            for t in pd.date_range(
                start=start_timestamp,
                periods=96,
                freq="15min",
                inclusive="left",
            )
        ]

        pgvs: list[models.PredictedGenerationValue] = [
            models.PredictedGenerationValue(
                location_uuid=location_uuid,
                valid_timestamp=t,
                power_kilowatts=float(i * 10),
                created_timestamp=created_timestamp,
                init_timestamp=created_timestamp,
                capacity_kilowatts=1000.0,
                forecaster_name="TestForecaster",
                forecaster_version="1.0",
            )
            for i, t in enumerate(times)
        ]

        result = format_csv_and_created_time(
            values=pgvs,
            forecast_horizon=models.ForecastHorizon.latest,
            tz=ZoneInfo("Asia/Kolkata"),
            now=now,
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
        rounded_15_min = (
            pd.Timestamp(
                now.astimezone(tz=ZoneInfo("Asia/Kolkata")),
            )
            .round("15min")
            .to_pydatetime()
        )
        assert result[0].iloc[0]["Time"] == str(
            rounded_15_min.astimezone(tz=ZoneInfo("Asia/Kolkata")).strftime("%H:%M")
            + " - "
            + (rounded_15_min + dt.timedelta(minutes=15))
            .astimezone(
                tz=ZoneInfo("Asia/Kolkata"),
            )
            .strftime("%H:%M"),
        )
        # Check the number of rows in the DataFrame
        # For the latest forecast, it should be the number of
        # forecast values after now
        forecast_values_from_now = [
            value for value in pgvs if value.valid_timestamp >= rounded_15_min
        ]
        assert result[0].shape[0] == len(forecast_values_from_now)
        # Check the column names
        assert list(result[0].columns) == ["Date [IST]", "Time", "PowerMW"]
        # Check the data types of the columns
        assert result[0]["Date [IST]"].dtype == "object"
        assert result[0]["Time"].dtype == "object"
        assert result[0]["PowerMW"].dtype == "float64"

    def test_format_csv_and_created_time_empty(self) -> None:
        """With no forecast values a header-only DataFrame is returned, not an error."""
        df, created_time = format_csv_and_created_time(
            values=[],
            forecast_horizon=models.ForecastHorizon.latest,
            tz=ZoneInfo("Asia/Kolkata"),
            now=dt.datetime(2021, 10, 1, 5, 30, 0, tzinfo=dt.UTC),
        )
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["Date [IST]", "Time", "PowerMW"]
        assert df.shape[0] == 0
        assert created_time is None

    def test_format_csv_and_created_time_day_ahead_no_matching_values(self) -> None:
        """Day-ahead with no values for tomorrow also returns a header-only DataFrame."""
        location_uuid = uuid4()
        now = dt.datetime(2021, 10, 1, 5, 30, 0, tzinfo=dt.UTC)
        # All values are for today, so none match the day-ahead (tomorrow) filter.
        pgvs = [
            models.PredictedGenerationValue(
                location_uuid=location_uuid,
                valid_timestamp=now,
                power_kilowatts=10.0,
                created_timestamp=now - dt.timedelta(hours=1),
                init_timestamp=now - dt.timedelta(hours=1),
                capacity_kilowatts=1000.0,
                forecaster_name="TestForecaster",
                forecaster_version="1.0",
            ),
        ]
        df, created_time = format_csv_and_created_time(
            values=pgvs,
            forecast_horizon=models.ForecastHorizon.day_ahead,
            tz=ZoneInfo("Asia/Kolkata"),
            now=now,
        )
        assert list(df.columns) == ["Date [IST]", "Time", "PowerMW"]
        assert df.shape[0] == 0
        assert created_time is None
