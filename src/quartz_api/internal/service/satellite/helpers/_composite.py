"""Blend single-channel tifs into the composite layers (visible, infrared, blue)."""
import io

import numpy as np
import rasterio

from quartz_api.internal.s3 import S3Client

from ..config import COMPOSITE_CONFIG, SAT_MAX_ALPHA, SAT_OPACITY
from .common import DST_SHAPE, build_layer_tif, save


def _flatten(greys: list[np.ndarray]) -> np.ndarray:
    """Alpha-blend member channels' [0, 1] greys (bottom-to-top) into one [0, 1] grey."""
    out_grey = np.zeros(DST_SHAPE, dtype=np.float32)
    out_alpha = np.zeros(DST_SHAPE, dtype=np.float32)
    for grey in greys:
        grey = np.nan_to_num(grey).astype(np.float32)
        alpha = grey * (SAT_MAX_ALPHA / 255.0) * SAT_OPACITY
        new_alpha = alpha + out_alpha * (1 - alpha)
        with np.errstate(invalid="ignore", divide="ignore"):
            out_grey = np.where(
                new_alpha > 0,
                (grey * alpha + out_grey * out_alpha * (1 - alpha)) / new_alpha,
                0.0,
            ).astype(np.float32)
        out_alpha = new_alpha
    return out_grey


def build_composites(
    s3: S3Client,
    geo_bucket: str,
    ts: str,
    uploaded: dict[str, set[str]],
    changed: set[str] | None = None,
) -> None:
    """Blend each composite's member tifs into one layer; rebuild if a member changed this run."""
    changed = changed or set()
    fname = f"{ts}.tif"
    for comp, members in COMPOSITE_CONFIG.items():
        present = [m for m in members if fname in uploaded[m]]
        if not present:
            continue
        # Skip only if already built and no member was (re)written this run.
        if fname in uploaded[comp] and not any(m in changed for m in members):
            continue

        greys = []
        for m in present:
            blob = s3.download_bytes(geo_bucket, f"layers/{m}/{fname}")
            with rasterio.open(io.BytesIO(blob)) as src:
                greys.append((src.read(1, masked=True).astype(np.float32).filled(np.nan) - 1) / 254)

        tif = build_layer_tif(
            comp, _flatten(greys), timestamp=ts,
            missing_channels=",".join(m for m in members if m not in present),
        )
        save(s3, geo_bucket, comp, ts, tif, uploaded)
