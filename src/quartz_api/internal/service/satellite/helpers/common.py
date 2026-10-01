"""Shared bits every ingest step needs: the output grid, tif building, and uploading."""
import io
import logging

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform_bounds

from quartz_api.internal.s3 import S3Client

from ..config import (
    BOTTOM,
    COMPOSITE_CONFIG,
    LAYER_CONFIG,
    LEFT,
    RESOLUTION_M,
    RIGHT,
    TOP,
)

log = logging.getLogger(__name__)

# Every layer we produce a tif for: the raw channels plus the blended composites.
LAYERS = (*LAYER_CONFIG, *COMPOSITE_CONFIG)
TS_FMT = "%Y%m%d_%H%M%S"

# Fixed EPSG:3857 output grid covering the Europe crop, shared by every tif we write.
DST_CRS = CRS.from_epsg(3857)
_x0, _y0, _x1, _y1 = transform_bounds("EPSG:4326", "EPSG:3857", LEFT, BOTTOM, RIGHT, TOP)
DST_TF = rasterio.transform.from_origin(_x0, _y1, RESOLUTION_M, RESOLUTION_M)
DST_SHAPE = (round((_y1 - _y0) / RESOLUTION_M), round((_x1 - _x0) / RESOLUTION_M))
BOUNDS_TAG = f"{LEFT},{BOTTOM},{RIGHT},{TOP}"
TIF_PROFILE = {
    "driver": "GTiff", "dtype": "uint8", "nodata": 0, "crs": "EPSG:3857", "transform": DST_TF,
    "height": DST_SHAPE[0], "width": DST_SHAPE[1],
    "compress": "jpeg", "jpeg_quality": 90, "photometric": "minisblack",
    "tiled": True, "blockxsize": 256, "blockysize": 256,
}


def tags(layer: str, **extra: str) -> dict[str, str]:
    """Tags every tif carries: what it is, where, and the value range its 1-255 maps to."""
    cfg = LAYER_CONFIG.get(layer, {"range": (0, 1)})  # composites are already 0-1
    lo, hi = cfg["range"]
    return {
        "channel": layer,
        "bounds_wgs84": BOUNDS_TAG,
        "scale_min": str(lo),
        "scale_max": str(hi),
        "inverted": str(cfg.get("invert", False)),
        **extra,
    }


def build_layer_tif(layer: str, grey: np.ndarray, **extra: str) -> bytes:
    """A [0, 1] grey as an 8-bit JPEG GeoTIFF: data 1-255, 0 = no data (NaN), plus tags."""
    finite = np.isfinite(grey)
    band = np.zeros(DST_SHAPE, dtype=np.uint8)
    band[finite] = np.round(1 + grey[finite] * 254)
    buf = io.BytesIO()
    with rasterio.open(buf, "w", count=1, **TIF_PROFILE) as dst:
        dst.write(band, 1)
        dst.update_tags(**tags(layer, **extra))
    return buf.getvalue()


def save(
    s3: S3Client, bucket: str, layer: str, ts: str, tif: bytes, uploaded: dict[str, set[str]],
) -> None:
    """Upload one layer's tif for a slot and record it as present."""
    s3.upload_bytes(bucket, f"layers/{layer}/{ts}.tif", tif)
    uploaded[layer].add(f"{ts}.tif")
    log.info("Uploaded %s @ %s", layer, ts)
