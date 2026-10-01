"""Rolling 48 h stacks: one multi-band tif per layer, and its presigned URL."""
import datetime as dt
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import rasterio

from quartz_api.internal.s3 import S3Client, get_geotiff_bucket, get_s3_client

from ..config import BACKFILL_HOURS, STACK_INTERVAL_MINUTES
from .common import LAYERS, TIF_PROFILE, TS_FMT, tags

log = logging.getLogger(__name__)


def rebuild_stacks(s3: S3Client, bucket: str) -> None:
    """Glue each layer's last BACKFILL_HOURS of 15-min tifs into one multi-band rolling stack."""
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=BACKFILL_HOURS)).strftime(TS_FMT)
    for layer in LAYERS:
        slots = sorted(
            ts
            for k in s3.list_keys(bucket, f"layers/{layer}/")
            if (ts := k.rsplit("/", 1)[-1].removesuffix(".tif")) >= cutoff
            and int(ts[11:13]) % STACK_INTERVAL_MINUTES == 0 and ts[13:] == "00"
        )
        if not slots:
            continue

        prefix = f"layers/{layer}/"
        with ThreadPoolExecutor(max_workers=16) as pool:
            frames = list(pool.map(
                lambda ts, p=prefix: s3.download_bytes(bucket, f"{p}{ts}.tif"), slots,
            ))
        bands = []
        for blob in frames:
            with rasterio.open(io.BytesIO(blob)) as src:
                bands.append(src.read(1))

        buf = io.BytesIO()
        with rasterio.open(buf, "w", count=len(bands), interleave="band", **TIF_PROFILE) as dst:
            dst.write(np.stack(bands))
            for i, ts in enumerate(slots, start=1):
                dst.set_band_description(i, ts)
            dst.update_tags(**tags(layer, timestamps=",".join(slots)))
        s3.upload_bytes(bucket, f"rolling/{layer}.tif", buf.getvalue())
        log.info("Uploaded rolling stack for %s (%d slots)", layer, len(slots))


def stack_url(layer: str) -> str | None:
    """Presigned URL (valid 1 h, re-signed every STACK_INTERVAL_MINUTES) of a layer's stack."""
    s3, bucket = get_s3_client(), get_geotiff_bucket()
    if not s3.object_exists(bucket, f"rolling/{layer}.tif"):
        return None
    return _signed_stack_url(layer, int(time.time() // (STACK_INTERVAL_MINUTES * 60)))


@lru_cache(maxsize=64)
def _signed_stack_url(layer: str, _window: int) -> str:
    s3, bucket = get_s3_client(), get_geotiff_bucket()
    return s3.get_presigned_url(bucket, f"rolling/{layer}.tif")
