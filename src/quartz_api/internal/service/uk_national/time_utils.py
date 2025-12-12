"""Utility functions for handling datetime objects in UK National context."""

from datetime import datetime

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
