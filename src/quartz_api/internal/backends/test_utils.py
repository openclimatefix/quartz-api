import datetime as dt

import time_machine

from quartz_api.internal.backends.utils import get_window


@time_machine.travel(dt.datetime(2023, 1, 1, tzinfo=dt.UTC), tick=False)
def test_get_window_defaults() -> None:
    start, end = get_window()
    assert start == dt.datetime(2022, 12, 30, tzinfo=dt.UTC)
    assert end == dt.datetime(2023, 1, 3, tzinfo=dt.UTC)
