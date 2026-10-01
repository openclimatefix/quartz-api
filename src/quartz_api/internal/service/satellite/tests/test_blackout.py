import datetime as dt
import unittest

from ..helpers._blackout import apply_buffer, sun_times

# Centre of the Europe bounding box the ingest uses for blackout.
EU_LON, EU_LAT = 2.5, 50.35

# Lit windows through the year, as the ingest builds them. Everything is UTC, so
# the days either side of a BST switchover must land on the same window as the
# switchover itself.
LIT_WINDOWS = [
    ("2026-01-01", "06:00", "18:00"),  # sun 07:50-15:56
    ("2026-02-14", "05:00", "19:00"),  # sun 07:03-17:05
    ("2026-03-20", "04:00", "20:00"),  # spring equinox, sun 05:53-18:02
    ("2026-03-28", "04:00", "20:00"),  # day before BST starts, sun 05:35-18:15
    ("2026-03-29", "04:00", "20:00"),  # BST starts, sun 05:33-18:17
    ("2026-03-30", "04:00", "20:00"),  # day after, sun 05:31-18:18
    ("2026-05-01", "02:00", "21:00"),  # sun 04:25-19:09
    ("2026-06-21", "02:00", "22:00"),  # summer solstice, longest day, sun 03:39-20:04
    ("2026-08-04", "02:00", "21:00"),  # sun 04:22-19:28
    ("2026-09-22", "04:00", "20:00"),  # autumn equinox, sun 05:36-17:48
    ("2026-10-24", "04:00", "19:00"),  # day before BST ends, sun 06:27-16:40
    ("2026-10-25", "04:00", "19:00"),  # BST ends, sun 06:28-16:38
    ("2026-10-26", "05:00", "19:00"),  # day after, sun 06:30-16:36
    ("2026-12-21", "06:00", "18:00"),  # winter solstice, shortest day, sun 07:47-15:48
]


class TestSunTimes(unittest.TestCase):
    def test_sunrise_and_sunset_over_europe(self) -> None:
        rise, set_ = sun_times(dt.date(2026, 6, 21), EU_LON, EU_LAT)

        self.assertEqual(rise.strftime("%H:%M"), "03:39")
        self.assertEqual(set_.strftime("%H:%M"), "20:04")


class TestApplyBuffer(unittest.TestCase):
    def test_sunrise_rounds_down_and_sunset_up(self) -> None:
        rise = dt.datetime(2026, 6, 21, 3, 32, tzinfo=dt.UTC)
        set_ = dt.datetime(2026, 6, 21, 20, 52, tzinfo=dt.UTC)

        self.assertEqual(
            apply_buffer(rise, set_),
            (
                dt.datetime(2026, 6, 21, 2, 0, tzinfo=dt.UTC),
                dt.datetime(2026, 6, 21, 23, 0, tzinfo=dt.UTC),
            ),
        )

    def test_events_already_on_the_hour(self) -> None:
        rise = dt.datetime(2026, 6, 21, 4, 0, tzinfo=dt.UTC)
        set_ = dt.datetime(2026, 6, 21, 20, 0, tzinfo=dt.UTC)

        self.assertEqual(
            apply_buffer(rise, set_),
            (
                dt.datetime(2026, 6, 21, 2, 0, tzinfo=dt.UTC),
                dt.datetime(2026, 6, 21, 22, 0, tzinfo=dt.UTC),
            ),
        )


class TestLitWindowThroughTheYear(unittest.TestCase):
    def test_windows(self) -> None:
        for day, first_light, last_light in LIT_WINDOWS:
            with self.subTest(day=day):
                start, end = apply_buffer(
                    *sun_times(dt.date.fromisoformat(day), EU_LON, EU_LAT),
                )
                self.assertEqual(
                    (start.strftime("%H:%M"), end.strftime("%H:%M")),
                    (first_light, last_light),
                )
