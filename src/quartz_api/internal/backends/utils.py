"""Utility functions..."""

import datetime as dt


# TODO remove this file and move start and end times to routers
def get_window() -> tuple[dt.datetime, dt.datetime]:
    """Returns the start and end of the window for timeseries data."""
    # Window start is the beginning of the day two days ago
    start = (dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    end = (dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2)).replace(
        hour=0,
        minute=0,
        second=0,
    microsecond=0,
    )

    return (start, end)

