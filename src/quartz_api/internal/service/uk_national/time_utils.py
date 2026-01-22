"""Utility functions for handling datetime objects in UK National context."""

import datetime as dt
import os

import pandas as pd
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

def get_start_window() ->  dt.datetime:
    """Returns the start of the window for timeseries data."""
    return pd.Timestamp.utcnow().floor("6h").to_pydatetime() - dt.timedelta(days=2)

def get_end_window() ->  dt.datetime:
    """Returns the end of the window for timeseries data."""
    return pd.Timestamp.utcnow().floor("6h").to_pydatetime() + dt.timedelta(days=2)

def get_now_floor_30_mins() ->  dt.datetime:
    """Returns the current time rounded down to the nearest 30 minutes."""
    return pd.Timestamp.utcnow().floor("30T").to_pydatetime()



