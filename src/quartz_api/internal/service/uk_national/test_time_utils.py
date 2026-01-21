import datetime as dt

from .time_utils import add_timezone, ceil_30_minutes_dt, floor_30_minutes_dt, get_window


def test_format_datetime():


    format_datetime_none = add_timezone(None)
    assert format_datetime_none is None

    format_datetime_no_tz = add_timezone("2024-01-01T12:00:00")
    assert format_datetime_no_tz.isoformat() == "2024-01-01T12:00:00+00:00"


def test_floor_30_minutes_dt():
    dt1 = floor_30_minutes_dt(add_timezone("2024-01-01T12:15:45+00:00"))
    assert dt1.isoformat() == "2024-01-01T12:00:00+00:00"

    dt2 = floor_30_minutes_dt(add_timezone("2024-01-01T12:45:30+00:00"))
    assert dt2.isoformat() == "2024-01-01T12:30:00+00:00"

    dt3 = floor_30_minutes_dt(add_timezone("2024-01-01T12:30:00+00:00"))
    assert dt3.isoformat() == "2024-01-01T12:30:00+00:00"


def test_ceil_30_minutes_dt():
    dt1 = ceil_30_minutes_dt(add_timezone("2024-01-01T12:15:45+00:00"))
    assert dt1.isoformat() == "2024-01-01T12:30:00+00:00"

    dt2 = ceil_30_minutes_dt(add_timezone("2024-01-01T12:45:30+00:00"))
    assert dt2.isoformat() == "2024-01-01T13:00:00+00:00"

    dt3 = ceil_30_minutes_dt(add_timezone("2024-01-01T12:30:00+00:00"))
    assert dt3.isoformat() == "2024-01-01T12:30:00+00:00"


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

