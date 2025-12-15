"""Utility functions for handling datetime objects in UK National context."""

from datetime import datetime, timedelta

import numpy as np
from pytz import timezone

utc = timezone("UTC")


def format_datetime(datetime_str: str | None = None) -> datetime | None:
    """Format datetime string to datetime object.

    If None return None, if not timezone, add UTC
    :param datetime_str: The datetime string to be formatted.
    :return: The formatted datetime object or None.
    """
    if datetime_str is None:
        return None

    else:
        datetime_output = datetime.fromisoformat(datetime_str)
        if datetime_output.tzinfo is None:
            datetime_output = utc.localize(datetime_output)
        return datetime_output


def floor_30_minutes_dt(dt: datetime) -> datetime:
    """Floor a datetime by 30 mins.

    For example:
    2021-01-01 17:01:01 --> 2021-01-01 17:00:00
    2021-01-01 17:35:01 --> 2021-01-01 17:30:00

    :param dt:
    :return:
    """
    approx = np.floor(dt.minute / 30.0) * 30
    dt = dt.replace(minute=0)
    dt = dt.replace(second=0)
    dt = dt.replace(microsecond=0)
    dt += timedelta(minutes=approx)

    return dt


def ceil_30_minutes_dt(dt: datetime) -> datetime:
    """Ceil a datetime by 30 mins.

    For example:
    2021-01-01 17:01:01 --> 2021-01-01 17:30:00
    2021-01-01 17:35:01 --> 2021-01-01 18:00:00
    2021-01-01 17:30:00 --> 2021-01-01 17:30:00
    """
    dt_floor = floor_30_minutes_dt(dt)
    if dt == dt_floor:
        return dt_floor
    dt_ceil = dt_floor + timedelta(minutes=30)
    return dt_ceil

