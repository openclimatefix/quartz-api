"""Module to smooth forecasted power values."""

import pandas as pd

from quartz_api.internal import models


def smooth_forecast(values: list[models.PredictedPower]) -> list[models.PredictedPower]:
    """Smooths the forecast values."""
    # convert to dataframe
    df = pd.DataFrame(
        {
            "Time": [value.Time for value in values],
            "PowerKW": [value.PowerKW for value in values],
        },
    )

    # smooth and make sure it is symmetrical
    df = df.set_index("Time")
    # try to do this in one step, but couldnt, center=True and closed='both' didnt work
    df = (df.rolling(4, min_periods=1).mean() + df[::-1].rolling(4, min_periods=1).mean()) / 2.0

    # convert to ints
    df["PowerKW"] = df["PowerKW"].astype(int)
    df["CreatedTime"] = [value.CreatedTime for value in values]

    # convert back to list of PredictedPower
    return [
        models.PredictedPower(
            Time=index,
            PowerKW=row.PowerKW,
            CreatedTime=row.CreatedTime,
        )
        for index, row in df.iterrows()
    ]
