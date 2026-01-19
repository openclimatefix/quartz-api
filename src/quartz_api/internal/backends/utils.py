"""Utility functions..."""

import datetime as dt

import numpy as np


def get_window(
    start: dt.datetime | None = None, end: dt.datetime | None = None,
) -> tuple[dt.datetime, dt.datetime]:
    """Returns the start and end of the window for timeseries data."""
    # Window start is the beginning of the day two days ago
    if start is None:
        start = (dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2))
        start = floor_6_hours_dt(start)

    # Window end is the beginning of the day two days ahead
    if end is None:
        end = start + dt.timedelta(days=4)

    return (start, end)


def floor_6_hours_dt(ts: dt.datetime) -> dt.datetime:
    """Floor a datetime by 6 hours.

    For example:
    2021-01-01 17:01:01 --> 2021-01-01 12:00:00
    2021-01-01 19:35:01 --> 2021-01-01 18:00:00

    :param dt: datetime
    :return: datetime rounded to lowest 6 hours
    """
    approx = np.floor(ts.hour / 6.0) * 6.0
    ts = ts.replace(hour=0)
    ts = ts.replace(minute=0)
    ts = ts.replace(second=0)
    ts = ts.replace(microsecond=0)
    ts += dt.timedelta(hours=approx)

    return ts
