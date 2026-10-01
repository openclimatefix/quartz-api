import unittest

import numpy as np

from ..helpers._composite import _flatten
from ..helpers.common import DST_SHAPE


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


if __name__ == "__main__":
    unittest.main()
