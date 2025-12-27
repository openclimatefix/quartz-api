import datetime as dt
import unittest

from .utils import get_window


class TestGetWindow(unittest.TestCase):
    def test_get_window_with_reference_time(self) -> None:
        reference_time = dt.datetime(2024, 1, 10, 15, 30, tzinfo=dt.UTC)

        start, end = get_window(reference_time=reference_time)

        self.assertEqual(
            start,
            dt.datetime(2024, 1, 8, 0, 0, tzinfo=dt.UTC),
        )
        self.assertEqual(
            end,
            dt.datetime(2024, 1, 12, 0, 0, tzinfo=dt.UTC),
        )
