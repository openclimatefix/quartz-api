import pandas as pd
from datetime import datetime

from quartz_api.internal import PredictedPower
from quartz_api.internal.models import ForecastHorizon


def format_csv_and_created_time(values: list[PredictedPower], forecast_horizon: ForecastHorizon) -> (pd.DataFrame, datetime):
    """
    Format the predicted power values into a pandas dataframe ready for CSV export.

    We also get the maximum created time of these forecasts

    The pandas dataframes ends up with
    - Date [IST]: The date
    - Time: start and end time, e.g 00:00 to 00:15
    - PowerMW, the forecasted power in MW

    """
    # change list of prediction power to pandas dataframe
    df = pd.DataFrame([y.__dict__ for y in values])

    # change Time columns from UTC to IST
    df["Time"] = pd.to_datetime(df["Time"]).dt.tz_convert("Asia/Kolkata")

    # create date columns
    df["Date [IST]"] = df["Time"].dt.date
    # create start and end time column and only show HH:MM
    df["Start Time [IST]"] = df["Time"].dt.strftime("%H:%M")
    df["End Time [IST]"] = (df["Time"] + pd.to_timedelta("15min")).dt.strftime("%H:%M")

    now_ist = pd.Timestamp.now(tz="Asia/Kolkata")
    if forecast_horizon == ForecastHorizon.day_ahead:
        # only get tomorrow's results, for IST time.
        tomorrow = now_ist + pd.Timedelta(days=1)
        df = df[df["Date [IST]"] == tomorrow.date()]
    elif forecast_horizon == ForecastHorizon.latest:
        # only get results from now onwards, for IST time.
        df = df[df["Time"] >= now_ist]

    # combine start and end times
    df["Time"] = df["Start Time [IST]"].astype(str) + " - " + df["End Time [IST]"].astype(str)

    # get the max created time
    created_time = df["CreatedTime"].max()

    # change KW to MW
    df["PowerMW"] = df["PowerKW"] / 1000

    # drop and order
    df = df.drop(columns=["CreatedTime", "Start Time [IST]", "End Time [IST]"])
    df = df[["Date [IST]", "Time", "PowerMW"]]

    return df, created_time
