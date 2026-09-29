"""Turn raw EUMETSAT .nat files into single-channel tifs on the Europe grid."""
import datetime as dt
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject
from satpy import Scene
from satpy.readers.core.eum import recarray2dict
from satpy.readers.core.seviri import (
    REPEAT_CYCLE_DURATION,
    REPEAT_CYCLE_DURATION_RSS,
    round_nom_time,
)
from satpy.readers.seviri_l1b_native_hdr import get_native_header, native_trailer

from quartz_api.internal.s3 import S3Client

from ..config import BOTTOM, LAYER_CONFIG, LEFT, RAW_PREFIX, RIGHT, TOP
from ._blackout import apply_buffer, sun_times
from .common import DST_CRS, DST_SHAPE, DST_TF, TS_FMT, build_layer_tif, save

HEADER = get_native_header(with_archive_header=True)


def _slot_time(s3: S3Client, bucket: str, key: str) -> dt.datetime:
    """The raw file's slot, from its header + trailer (= satpy's nominal_end_time)."""
    size = s3.size(bucket, key)
    header = recarray2dict(np.frombuffer(s3.read_range(bucket, key, 0, HEADER.itemsize), HEADER))
    trailer_bytes = s3.read_range(bucket, key, size - native_trailer.itemsize, size)
    trailer = recarray2dict(np.frombuffer(trailer_bytes, native_trailer))

    scan = trailer["15TRAILER"]["ImageProductionStats"]["ActualScanningSummary"]
    cycle = REPEAT_CYCLE_DURATION_RSS if scan["ReducedScan"] == 1 else REPEAT_CYCLE_DURATION
    planned_end = header["15_DATA_HEADER"]["ImageAcquisition"]["PlannedAcquisitionTime"][
        "PlannedRepeatCycleEnd"
    ]
    return round_nom_time(planned_end, dt.timedelta(minutes=cycle)).replace(tzinfo=dt.UTC)


def list_raw_files(
    s3: S3Client, icechunk_bucket: str, sat_type: str, cutoff: dt.datetime,
) -> list[tuple[dt.datetime, str]]:
    """(slot, key) for every raw file whose header slot is at or after cutoff, oldest first."""
    keys = []
    for k in s3.list_keys(icechunk_bucket, RAW_PREFIX[sat_type]):
        # scan-end from the filename, tz attached below; only a coarse pre-filter
        scan_end = dt.datetime.strptime(k.rsplit("-NA-", 1)[1][:14], "%Y%m%d%H%M%S")  # noqa: DTZ007
        if scan_end.replace(tzinfo=dt.UTC) >= cutoff - dt.timedelta(hours=1):
            keys.append(k.removeprefix(f"{icechunk_bucket}/"))

    with ThreadPoolExecutor(max_workers=16) as pool:
        slots = pool.map(lambda k: _slot_time(s3, icechunk_bucket, k), keys)
        return sorted((t, k) for t, k in zip(slots, keys, strict=True) if t >= cutoff)


def ingest_channels(
    s3: S3Client,
    icechunk_bucket: str,
    geo_bucket: str,
    key: str,
    slot: dt.datetime,
    uploaded: dict[str, set[str]],
) -> None:
    """Decode one raw file, reproject each still-missing channel to the Europe grid, and upload."""
    ts = slot.strftime(TS_FMT)
    todo = [ch for ch in LAYER_CONFIG if f"{ts}.tif" not in uploaded[ch]]
    if not todo:
        return

    sunrise, sunset = apply_buffer(*sun_times(slot.date(), (LEFT + RIGHT) / 2, (BOTTOM + TOP) / 2))
    dark = not sunrise <= slot < sunset
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, key.rsplit("/", 1)[-1])
        with open(path, "wb") as f:
            f.write(s3.download_bytes(icechunk_bucket, key))
        scn = Scene(filenames=[path], reader="seviri_l1b_native")
        scn.load(todo)  # lazy - blacked-out channels are never actually decoded

        area = scn[todo[0]].attrs["area"]
        src_crs = CRS.from_wkt(area.crs.to_wkt())
        x0, y0, x1, y1 = area.area_extent
        src_tf = rasterio.transform.from_origin(
            x0, y1, (x1 - x0) / area.width, (y1 - y0) / area.height,
        )
        for ch in todo:
            cfg = LAYER_CONFIG[ch]
            if dark and cfg.get("blackout"):
                grey = np.zeros(DST_SHAPE, dtype=np.float32)
            else:
                out = np.full(DST_SHAPE, np.nan, dtype=np.float32)
                # average, not bilinear: each output pixel covers several source pixels
                reproject(
                    scn[ch].values.astype(np.float32), out,
                    src_transform=src_tf, src_crs=src_crs,
                    dst_transform=DST_TF, dst_crs=DST_CRS,
                    resampling=Resampling.average, src_nodata=np.nan, dst_nodata=np.nan,
                )
                lo, hi = cfg["range"]
                grey = np.clip((out - lo) / (hi - lo), 0, 1)
                if cfg.get("invert"):
                    grey = 1 - grey
            save(s3, geo_bucket, ch, ts, build_layer_tif(ch, grey, timestamp=ts), uploaded)
