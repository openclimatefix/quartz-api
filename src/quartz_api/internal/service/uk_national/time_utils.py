"""Utility functions for handling datetime objects in UK National context."""

import os
from datetime import UTC, datetime, timedelta

import numpy as np
import sentry_sdk
from pytz import timezone

utc = timezone("UTC")
# TODO this would be nice if this was done with the high level quartz-api config file
# One idea is we could put end_datetime_utc with auth into some middleware,
# and the time clipping is done then
INTRADAY_LIMIT_HOURS = float(os.getenv("INTRADAY_LIMIT_HOURS", 8))


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


def limit_end_datetime_by_permissions(
    permissions: list[str],
    end_datetime_utc: datetime | None = None,
    intraday_limit_hours: int = INTRADAY_LIMIT_HOURS,
) -> datetime:
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

    intraday_max_allowed = datetime.now(UTC) + timedelta(hours=intraday_limit_hours)
    if is_intraday_only_user:
        if end_datetime_utc is None:
            return intraday_max_allowed
        else:
            return min(end_datetime_utc, intraday_max_allowed)

    return end_datetime_utc



def floor_6_hours_dt(ts: datetime) -> datetime:
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
    ts += timedelta(hours=approx)

    return ts

def get_window(
    start: datetime | None = None, end: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Returns the start and end of the window for timeseries data."""
    # Window start is the beginning of the day two days ago
    if start is None:
        start = (datetime.now(tz=UTC) - timedelta(days=2))
        start = floor_6_hours_dt(start)

    # Window end is the beginning of the day two days ahead
    if end is None:
        end = start + timedelta(days=4)

    return (start, end)
