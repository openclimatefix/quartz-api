"""Functions to format predicted power data for CSV export."""

import datetime as dt

import pandas as pd

from quartz_api.internal import models


def format_csv_and_created_time(
    values: list[models.PredictedGenerationValue],
    forecast_horizon: models.ForecastHorizon,
    tz: models.TZDependency,
    now: dt.datetime | None = None,
) -> tuple[pd.DataFrame, dt.datetime | None]:
    """Format the predicted power values into a pandas dataframe ready for CSV export.

    We also get the maximum created time of these forecasts

    The pandas dataframes ends up with
    - Date [IST]: The date
    - Time: start and end time, e.g 00:00 to 00:15
    - PowerMW, the forecasted power in MW

    """
    if now is None:
        now = dt.datetime.now(tz=tz)

    # Change list of prediction power to pandas dataframe
    if forecast_horizon == models.ForecastHorizon.day_ahead:
        # For day ahead, we only want values from 00:00 to 23:45 tomorrow, in the given timezone
        wanted_values = [
            v
            for v in values
            if v.valid_timestamp.astimezone(tz=tz).date() == (now + dt.timedelta(days=1)).date()
        ]
    elif forecast_horizon == models.ForecastHorizon.latest:
        # For latest, we want all values from now onwards, in the given timezone
        wanted_values = [v for v in values if v.valid_timestamp.astimezone(tz=tz) >= now]
    else:
        wanted_values = values

    # With no forecast values, an empty DataFrame has no columns, so return a
    # header-only frame (and no created time) rather than raising a KeyError.
    if not wanted_values:
        df = pd.DataFrame(columns=["Date [IST]", "Time", "PowerMW"])
        return df, None

    # NOTE: Now this takes a timezone arg, [IST] here is potentially incorrect!
    df = pd.DataFrame.from_records(
        [
            {
                "Date [IST]": v.valid_timestamp.astimezone(tz=tz).date(),
                "PowerMW": round(v.power_kilowatts / 1000.0, 4),
                "Time": str(
                    v.valid_timestamp.astimezone(tz=tz).strftime("%H:%M")
                    + " - "
                    + (v.valid_timestamp + dt.timedelta(minutes=15))
                    .astimezone(tz=tz)
                    .strftime("%H:%M"),
                ),
                "CreatedTime": v.created_timestamp.astimezone(tz=tz),
            }
            for v in wanted_values
        ],
    )

    # Get the max created time
    created_time = df["CreatedTime"].max()
    df = df[["Date [IST]", "Time", "PowerMW"]]

    return df, created_time
