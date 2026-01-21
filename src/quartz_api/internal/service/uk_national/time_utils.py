"""Utility functions for handling datetime objects in UK National context."""

import datetime as dt
import os

import numpy as np
import sentry_sdk
from pytz import timezone

utc = timezone("UTC")
# TODO this would be nice if this was done with the high level quartz-api config file
# One idea is we could put end_datetime_utc with auth into some middleware,
# and the time clipping is done then
INTRADAY_LIMIT_HOURS = float(os.getenv("INTRADAY_LIMIT_HOURS", 8))


def add_timezone(datetime: str | dt.datetime | None = None) -> dt.datetime | None:
    """Add imezone to datetime.

    If None return None, if not timezone, add UTC
    :param datetime: The datetime string to be formatted.
    :return: The formatted datetime object or None.
    """
    if datetime is None:
        return None

    else:
        if datetime.tzinfo is None:
            datetime = utc.localize(datetime)
        return datetime


def floor_30_minutes_dt(ts: dt.datetime) -> dt.datetime:
    """Floor a datetime by 30 mins.

    For example:
    2021-01-01 17:01:01 --> 2021-01-01 17:00:00
    2021-01-01 17:35:01 --> 2021-01-01 17:30:00

    :param dt:
    :return:
    """
    approx = np.floor(ts.minute / 30.0) * 30
    ts = ts.replace(minute=0)
    ts = ts.replace(second=0)
    ts = ts.replace(microsecond=0)
    ts += dt.timedelta(minutes=approx)

    return ts


def ceil_30_minutes_dt(ts: dt.datetime) -> dt.datetime:
    """Ceil a datetime by 30 mins.

    For example:
    2021-01-01 17:01:01 --> 2021-01-01 17:30:00
    2021-01-01 17:35:01 --> 2021-01-01 18:00:00
    2021-01-01 17:30:00 --> 2021-01-01 17:30:00
    """
    ts_floor = floor_30_minutes_dt(ts)
    if ts == ts_floor:
        return ts_floor
    ts_ceil = ts_floor + dt.timedelta(minutes=30)
    return ts_ceil


def limit_end_datetime_by_permissions(
    permissions: list[str],
    end_datetime_utc: dt.datetime | None = None,
    intraday_limit_hours: int = INTRADAY_LIMIT_HOURS,
) -> dt.datetime:
    """Limit end datetime so that intraday users can receive forecast values max.

    Check if end_datetime_utc is set; if set, check it's not more than 8 hours from now,
    and if not set, set it to 8 hours from now.

    :param permissions: list of permissions, e.g. ['read:uk-intraday']
    :param end_datetime_utc: datetime, requested end time of forecast
    :param intraday_limit_hours: int, maximum number of hours allowed ahead of now for forecasts
    :return: datetime, end time of forecast, limited to max 8 hours from now
    """
    if permissions is None or len(permissions) == 0:
        sentry_sdk.capture_message(
            "User has no permissions during limit_end_datetime_by_permissions check;"
            "by default, users should have at least one role, so check in Auth0.",
        )
        return end_datetime_utc

    is_intraday_only_user = "read:uk-intraday" in permissions

    intraday_max_allowed = dt.datetime.now(dt.UTC) + dt.timedelta(hours=intraday_limit_hours)
    if is_intraday_only_user:
        if end_datetime_utc is None:
            return intraday_max_allowed
        else:
            return min(end_datetime_utc, intraday_max_allowed)

    return end_datetime_utc



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
