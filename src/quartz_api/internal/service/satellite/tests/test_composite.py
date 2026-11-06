import unittest

import numpy as np

from ..config import COMPOSITE_CONFIG
from ..helpers._composite import _flatten, build_composites
from ..helpers.common import DST_SHAPE, LAYERS, build_layer_tif


class FakeS3:
    def __init__(self, store: dict[str, bytes]) -> None:
        self.store = store
        self.uploads: dict[str, bytes] = {}

    def download_bytes(self, _bucket: str, key: str) -> bytes:
        return self.store[key]

    def upload_bytes(self, _bucket: str, key: str, data: bytes) -> None:
        self.uploads[key] = data
        self.store[key] = data


class TestFlatten(unittest.TestCase):
    def test_single_channel_passes_through(self) -> None:
        # One layer: the blend reduces to the layer itself (zeros stay zero).
        grey = np.clip(np.random.default_rng(0).random(DST_SHAPE), 0, 1).astype(np.float32)
        out = _flatten([grey])

        self.assertEqual(out.shape, DST_SHAPE)
        np.testing.assert_allclose(out, grey, atol=1e-6)

    def test_opaque_top_covers_bottom(self) -> None:
        # A full-brightness top layer dominates whatever is beneath it.
        bottom = np.zeros(DST_SHAPE, dtype=np.float32)
        top = np.ones(DST_SHAPE, dtype=np.float32)
        out = _flatten([bottom, top])

        self.assertGreater(out.mean(), 0.5)

    def test_nan_is_treated_as_empty(self) -> None:
        # NaNs must not propagate - they count as "nothing here", i.e. 0.
        grey = np.full(DST_SHAPE, np.nan, dtype=np.float32)
        out = _flatten([grey])

        self.assertTrue(np.isfinite(out).all())
        np.testing.assert_array_equal(out, np.zeros(DST_SHAPE, dtype=np.float32))


class TestBuildCompositesRebuild(unittest.TestCase):
    TS = "20260101_120000"

    def _setup(self) -> tuple[FakeS3, dict[str, set[str]], str, list[str]]:
        comp = "COMPOSITE_BLUE"
        members = COMPOSITE_CONFIG[comp]
        grey = np.zeros(DST_SHAPE, dtype=np.float32)
        fname = f"{self.TS}.tif"
        store = {f"layers/{m}/{fname}": build_layer_tif(m, grey) for m in members}
        s3 = FakeS3(store)
        uploaded: dict[str, set[str]] = {layer: set() for layer in LAYERS}
        for m in members:
            uploaded[m].add(fname)
        return s3, uploaded, comp, members

    def test_builds_then_skips_when_nothing_changed(self) -> None:
        s3, uploaded, comp, _ = self._setup()
        fname = f"{self.TS}.tif"

        build_composites(s3, "bucket", self.TS, uploaded)
        self.assertIn(f"layers/{comp}/{fname}", s3.uploads)
        self.assertIn(fname, uploaded[comp])

        s3.uploads.clear()
        build_composites(s3, "bucket", self.TS, uploaded)  # no `changed`
        self.assertEqual(s3.uploads, {})  # already built, nothing changed -> skip

    def test_rebuilds_when_a_member_changed(self) -> None:
        s3, uploaded, comp, members = self._setup()
        fname = f"{self.TS}.tif"

        build_composites(s3, "bucket", self.TS, uploaded)
        s3.uploads.clear()

        build_composites(s3, "bucket", self.TS, uploaded, changed={members[0]})
        self.assertIn(f"layers/{comp}/{fname}", s3.uploads)  # member changed -> rebuilt


if __name__ == "__main__":
    unittest.main()
