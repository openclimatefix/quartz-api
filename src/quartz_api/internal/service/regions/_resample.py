"""Functions to resample data."""

import datetime as dt
import math
from collections import defaultdict

from quartz_api.internal import models


def resample_generation(
    values: list[models.ActualPower],
    interval_minutes: int,
) -> list[models.ActualPower]:
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

    results: list[models.ActualPower] = []
    for bucket_time in sorted(buckets.keys()):
        avg_power = sum(buckets[bucket_time]) / len(buckets[bucket_time])
        if avg_power < 0:
            avg_power = 0.0

        results.append(models.ActualPower(Time=bucket_time, PowerKW=avg_power))

    return results

