import datetime as dt
import unittest

from ..helpers.common import TS_FMT
from ..helpers.ingest import _find_missing_timestamps


def _slot(base: dt.datetime, minutes: int) -> str:
    return f"{(base - dt.timedelta(minutes=minutes)):{TS_FMT}}.tif"


class TestFindMissingTimestamps(unittest.TestCase):
    def setUp(self) -> None:
        # The current 15-min slot: newest dummy sits at "now" so the gap check (which
        # runs up to now-30min) adds no phantom gaps after the last timestamp.
        now = dt.datetime.now(dt.UTC).replace(second=0, microsecond=0)
        self.base = now - dt.timedelta(minutes=now.minute % 15)

    def test_contiguous_run_has_no_gaps(self) -> None:
        keys = [_slot(self.base, m) for m in (0, 15, 30, 45)]

        self.assertEqual(_find_missing_timestamps(keys, backfill_hours=48), [])

    def test_interior_gap_is_reported(self) -> None:
        # Drop the 15-min-ago slot; it should be the only one flagged missing.
        keys = [_slot(self.base, m) for m in (0, 30, 45)]
        missing = _find_missing_timestamps(keys, backfill_hours=48)

        self.assertEqual(len(missing), 1)
        self.assertEqual(f"{missing[0]:{TS_FMT}}", _slot(self.base, 15).removesuffix(".tif"))

    def test_unaligned_and_non_tif_keys_ignored(self) -> None:
        keys = [_slot(self.base, 0), "layers/x/20260101_080732.tif", "notes.txt"]

        self.assertEqual(_find_missing_timestamps(keys, backfill_hours=48), [])


if __name__ == "__main__":
    unittest.main()
