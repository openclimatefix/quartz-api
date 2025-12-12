import datetime as dt

from freezegun import freeze_time

from quartz_api.internal.backends.utils import get_window


def test_get_window_defaults() -> None:
    with freeze_time("2023-01-01"):
        start, end = get_window()
        assert start == dt.datetime(2022, 12, 30, tzinfo=dt.UTC)
        assert end == dt.datetime(2023, 1, 3, tzinfo=dt.UTC)


def test_get_window_with_params() -> None:
    custom_start = dt.datetime(2023, 2, 1, 12, tzinfo=dt.UTC)
    custom_end = dt.datetime(2023, 2, 5, 12, tzinfo=dt.UTC)
    start, end = get_window(start=custom_start, end=custom_end)
    assert start == custom_start
    assert end == custom_end


def test_get_window_with_partial_params() -> None:
    custom_start = dt.datetime(2023, 3, 1, 8, tzinfo=dt.UTC)
    start, end = get_window(start=custom_start)
    assert start == custom_start
    assert end == custom_start + dt.timedelta(days=4)
