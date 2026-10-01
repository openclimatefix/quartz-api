import datetime as dt
import io
import unittest

import numpy as np
import rasterio

from ..helpers._stack import rebuild_stacks
from ..helpers.common import DST_SHAPE, TS_FMT, build_layer_tif


class FakeS3:
    """Minimal in-memory stand-in for S3Client: the few calls rebuild_stacks makes."""

    def __init__(self, store: dict[str, bytes]) -> None:
        self.store = store
        self.uploads: dict[str, bytes] = {}

    def list_keys(self, _bucket: str, prefix: str) -> list[str]:
        return [k for k in self.store if k.startswith(prefix)]

    def download_bytes(self, _bucket: str, key: str) -> bytes:
        return self.store[key]

    def upload_bytes(self, _bucket: str, key: str, data: bytes) -> None:
        self.uploads[key] = data


class TestRebuildStacks(unittest.TestCase):
    def test_stack_has_one_band_per_slot_in_order(self) -> None:
        now = dt.datetime.now(dt.UTC).replace(second=0, microsecond=0)
        base = now - dt.timedelta(minutes=now.minute % 15)
        slots = [f"{base - dt.timedelta(minutes=m):{TS_FMT}}" for m in (30, 15, 0)]

        grey = np.linspace(0, 1, DST_SHAPE[0] * DST_SHAPE[1]).reshape(DST_SHAPE).astype(np.float32)
        store = {f"layers/IR_108/{ts}.tif": build_layer_tif("IR_108", grey) for ts in slots}
        s3 = FakeS3(store)

        rebuild_stacks(s3, "bucket")

        self.assertIn("rolling/IR_108.tif", s3.uploads)
        with rasterio.open(io.BytesIO(s3.uploads["rolling/IR_108.tif"])) as src:
            expected = sorted(slots)
            self.assertEqual(src.count, len(expected))
            self.assertEqual(list(src.descriptions), expected)
            self.assertEqual(src.tags()["timestamps"], ",".join(expected))
            self.assertEqual(src.tags()["channel"], "IR_108")

    def test_no_tifs_means_no_stack(self) -> None:
        s3 = FakeS3({})

        rebuild_stacks(s3, "bucket")

        self.assertEqual(s3.uploads, {})


if __name__ == "__main__":
    unittest.main()
