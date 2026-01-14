"""Module to smooth forecasted power values."""

import pandas as pd

from quartz_api.internal import models


def smooth_forecast(values: list[models.PredictedPower]) -> list[models.PredictedPower]:
    """Smooths the forecast values."""
    # convert to dataframe
    df = pd.DataFrame(
        {
            "time": [value.time for value in values],
            "power_kW": [value.power_kW for value in values],
        },
    )

    # smooth and make sure it is symmetrical
    df = df.set_index("time")
    # try to do this in one step, but couldnt, center=True and closed='both' didnt work
    df = (df.rolling(4, min_periods=1).mean() + df[::-1].rolling(4, min_periods=1).mean()) / 2.0

    # convert to ints
    df["power_kW"] = df["power_kW"].astype(int)
    df["created_time"] = [value.created_time for value in values]

    # convert back to list of PredictedPower
    return [
        models.PredictedPower(
            time=index,
            power_kW=row.power_kW,
            created_time=row.created_time,
        )
        for index, row in df.iterrows()
    ]
