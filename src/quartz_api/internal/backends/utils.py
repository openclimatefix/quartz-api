"""Utility functions..."""

import datetime as dt


def get_window(
    reference_time: dt.datetime | None = None,
) -> tuple[dt.datetime, dt.datetime]:
    """Returns the start and end of the window for timeseries data.

    Args:
        reference_time: The time to base the window on. If None, defaults to now (UTC).
    """
    if reference_time is None:
        reference_time = dt.datetime.now(tz=dt.UTC)

    # Window start is the beginning of the day two days ago
    start = (reference_time - dt.timedelta(days=2)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    # Window end is the beginning of the day two days ahead
    end = (reference_time + dt.timedelta(days=2)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return (start, end)
