from .time_utils import ceil_30_minutes_dt, floor_30_minutes_dt, format_datetime


def test_format_datetime():


    format_datetime_none = format_datetime(None)
    assert format_datetime_none is None

    format_datetime_no_tz = format_datetime("2024-01-01T12:00:00")
    assert format_datetime_no_tz.isoformat() == "2024-01-01T12:00:00+00:00"


def test_floor_30_minutes_dt():
    dt1 = floor_30_minutes_dt(format_datetime("2024-01-01T12:15:45+00:00"))
    assert dt1.isoformat() == "2024-01-01T12:00:00+00:00"

    dt2 = floor_30_minutes_dt(format_datetime("2024-01-01T12:45:30+00:00"))
    assert dt2.isoformat() == "2024-01-01T12:30:00+00:00"

    dt3 = floor_30_minutes_dt(format_datetime("2024-01-01T12:30:00+00:00"))
    assert dt3.isoformat() == "2024-01-01T12:30:00+00:00"


def test_ceil_30_minutes_dt():
    dt1 = ceil_30_minutes_dt(format_datetime("2024-01-01T12:15:45+00:00"))
    assert dt1.isoformat() == "2024-01-01T12:30:00+00:00"

    dt2 = ceil_30_minutes_dt(format_datetime("2024-01-01T12:45:30+00:00"))
    assert dt2.isoformat() == "2024-01-01T13:00:00+00:00"

    dt3 = ceil_30_minutes_dt(format_datetime("2024-01-01T12:30:00+00:00"))
    assert dt3.isoformat() == "2024-01-01T12:30:00+00:00"
