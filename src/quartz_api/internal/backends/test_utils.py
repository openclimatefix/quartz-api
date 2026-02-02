import datetime as dt

from freezegun import freeze_time

from quartz_api.internal.backends.utils import get_window


def test_get_window_defaults() -> None:
    with freeze_time("2023-01-01"):
        start, end = get_window()
        assert start == dt.datetime(2022, 12, 30, tzinfo=dt.UTC)
        assert end == dt.datetime(2023, 1, 3, tzinfo=dt.UTC)
