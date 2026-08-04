import datetime as dt
import unittest

from ._blackout import apply_buffer, sun_times

# Middle of the UK bounding box used by the ingest.
UK_LON, UK_LAT = -2.725, 54.9

# Lit windows through the year, as the ingest builds them. Everything is UTC, so
# the days either side of a BST switchover must land on the same window as the
# switchover itself.
LIT_WINDOWS = [
    ("2026-01-01", "07:00", "17:00"),  # sun 08:35-15:53
    ("2026-02-14", "06:00", "19:00"),  # sun 07:35-17:15
    ("2026-03-20", "05:00", "20:00"),  # spring equinox, sun 06:13-18:24
    ("2026-03-28", "04:00", "20:00"),  # day before BST starts, sun 05:53-18:39
    ("2026-03-29", "04:00", "20:00"),  # BST starts, sun 05:50-18:41
    ("2026-03-30", "04:00", "20:00"),  # day after, sun 05:48-18:43
    ("2026-05-01", "03:00", "21:00"),  # sun 04:31-19:45
    ("2026-06-21", "02:00", "22:00"),  # summer solstice, longest day, sun 03:32-20:52
    ("2026-08-04", "03:00", "22:00"),  # sun 04:25-20:06
    ("2026-09-22", "04:00", "20:00"),  # autumn equinox, sun 05:56-18:09
    ("2026-10-24", "05:00", "18:00"),  # day before BST ends, sun 06:58-16:50
    ("2026-10-25", "06:00", "18:00"),  # BST ends, sun 07:00-16:48
    ("2026-10-26", "06:00", "18:00"),  # day after, sun 07:02-16:46
    ("2026-12-21", "07:00", "17:00"),  # winter solstice, shortest day, sun 08:33-15:44
]


class TestSunTimes(unittest.TestCase):
    def test_sunrise_and_sunset_over_the_uk(self) -> None:
        rise, set_ = sun_times(dt.date(2026, 6, 21), UK_LON, UK_LAT)

        self.assertEqual(rise.strftime("%H:%M"), "03:32")
        self.assertEqual(set_.strftime("%H:%M"), "20:52")


class TestApplyBuffer(unittest.TestCase):
    def test_sunrise_rounds_down_and_sunset_up(self) -> None:
        rise = dt.datetime(2026, 6, 21, 3, 32, tzinfo=dt.UTC)
        set_ = dt.datetime(2026, 6, 21, 20, 52, tzinfo=dt.UTC)

        self.assertEqual(
            apply_buffer(rise, set_),
            (
                dt.datetime(2026, 6, 21, 2, 0, tzinfo=dt.UTC),
                dt.datetime(2026, 6, 21, 22, 0, tzinfo=dt.UTC),
            ),
        )

    def test_events_already_on_the_hour(self) -> None:
        rise = dt.datetime(2026, 6, 21, 4, 0, tzinfo=dt.UTC)
        set_ = dt.datetime(2026, 6, 21, 20, 0, tzinfo=dt.UTC)

        self.assertEqual(
            apply_buffer(rise, set_),
            (
                dt.datetime(2026, 6, 21, 3, 0, tzinfo=dt.UTC),
                dt.datetime(2026, 6, 21, 21, 0, tzinfo=dt.UTC),
            ),
        )


class TestLitWindowThroughTheYear(unittest.TestCase):
    def test_windows(self) -> None:
        for day, first_light, last_light in LIT_WINDOWS:
            with self.subTest(day=day):
                start, end = apply_buffer(
                    *sun_times(dt.date.fromisoformat(day), UK_LON, UK_LAT),
                )
                self.assertEqual(
                    (start.strftime("%H:%M"), end.strftime("%H:%M")),
                    (first_light, last_light),
                )
