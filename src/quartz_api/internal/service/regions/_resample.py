"""Functions to resample data."""

import datetime as dt
import math
from collections import defaultdict

import pandas as pd

from .endpoint_types import ActualPower, PredictedPower


def resample_generation(
    values: list[ActualPower],
    interval_minutes: int,
) -> list[ActualPower]:
    """Perform binning on the generation data, with a specified bin width."""
    if not values:
        return []

    buckets: dict[dt.datetime, list[float]] = defaultdict(list[float])
    interval_seconds = interval_minutes * 60

    for value in values:
        ts = value.Time.timestamp()
        floored_ts = math.floor(ts / interval_seconds) * interval_seconds
        bucket_time = dt.datetime.fromtimestamp(floored_ts, tz=value.Time.tzinfo)
        buckets[bucket_time].append(value.PowerKW)

    results: list[ActualPower] = []
    for bucket_time in sorted(buckets.keys()):
        avg_power = sum(buckets[bucket_time]) / len(buckets[bucket_time])
        if avg_power < 0:
            avg_power = 0.0

        results.append(
            ActualPower(
                Time=bucket_time,
                PowerKW=avg_power,
                location_uuid=values[0].location_uuid,
            ),
        )

    return results


def smooth_forecast(values: list[PredictedPower]) -> list[PredictedPower]:
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
    df["created_time"] = [value.created_time for value in values]
    df["initialization_timestamp_utc"] = [value.initialization_timestamp_utc for value in values]
    df["forecaster_name"] = [value.forecaster_name for value in values]
    df["forecaster_version"] = [value.forecaster_version for value in values]
    df["plevel_kW"] = [value.plevel_kW for value in values]

    # convert back to list of PredictedPower
    return [
        PredictedPower(
            Time=index,
            PowerKW=row.PowerKW,
            created_time=row.created_time,
            initialization_timestamp_utc=row.initialization_timestamp_utc,
            forecaster_name=row.forecaster_name,
            forecaster_version=row.forecaster_version,
            plevel_kW=row.plevel_kW,
        )
        for index, row in df.iterrows()
    ]
