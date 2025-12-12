"""Utility functions..."""

import datetime as dt


def get_window(
    start: dt.datetime | None = None, end: dt.datetime | None = None,
) -> tuple[dt.datetime, dt.datetime]:
    """Returns the start and end of the window for timeseries data."""
    # Window start is the beginning of the day two days ago
    if start is None:
        start = (dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    # Window end is the beginning of the day two days ahead
    if end is None:
        end = start + dt.timedelta(days=4)

    return (start, end)
