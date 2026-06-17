"""Ingest latest satellite data for all channels into S3."""
import io
import logging

import icechunk
import numpy as np
import rasterio
import sentry_sdk
import xarray as xr
from rasterio.crs import CRS
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds
from rasterio.windows import from_bounds as window_from_bounds

from quartz_api.internal.s3 import (
    get_geotiff_bucket,
    get_icechunk_bucket,
    get_icechunk_prefix,
    get_region,
    get_s3_client,
)

log = logging.getLogger(__name__)

# Bounding box to crop to UK
LEFT, BOTTOM, RIGHT, TOP = -17.05, 46.49, 11.60, 63.31
# How far back to backfill missing data (in hours)
BACKFILL_HOURS = 4
# config to remove artifacts during nighttime
SOLAR_NOISE_CONFIG = {
    "VIS006": {"percentile": 99.0, "cliff_limit": 5.0},
    "VIS008": {"percentile": 99.0, "cliff_limit": 10.0},
    "IR_016": {"percentile": 99.0, "cliff_limit": 12.0},
}

_ingest_running: bool = False


def run_ingest() -> tuple[str, str]:
    """Run ingest of latest satellite data for all channels.

    Uploads any missing channel+timestamp combos from the last 4 hours,
    skipping ones already present in S3.

    Returns:
        Tuple of (latest timestamp, ts_str).
    """
    global _ingest_running
    if _ingest_running:
        log.info("Ingest already running, skipping")
        return "", ""
    _ingest_running = True
    try:
        return _run_ingest()
    finally:
        _ingest_running = False


def _run_ingest() -> tuple[str, str]:
    log.info("Ingest started")

    s3_bucket = get_geotiff_bucket()
    icechunk_bucket = get_icechunk_bucket()
    icechunk_prefix = get_icechunk_prefix()
    region = get_region()
    s3_client = get_s3_client()

    repo = icechunk.Repository.open(
        storage=icechunk.s3_storage(
            bucket=icechunk_bucket,
            prefix=icechunk_prefix,
            **({"region": region} if region else {}),
        ),
    )
    ds = xr.open_zarr(repo.readonly_session("main").store)

    proj = ds.attrs["area"]["msg_seviri_fes_3km"]["projection"]
    src_crs = CRS.from_proj4(
        f"+proj=geos +lon_0={proj['lon_0']} +h={proj['h']} "
        f"+x_0={proj['x_0']} +y_0={proj['y_0']} "
        f"+a={proj['a']} +rf={proj['rf']}",
    )

    x = ds["x_geostationary"].values
    y = ds["y_geostationary"].values
    xres = abs(x[1] - x[0])
    yres = abs(y[1] - y[0])
    src_tf = rasterio.transform.from_origin(
        x.min() - xres / 2, y.max() + yres / 2, xres, yres,
    )

    dst_crs = CRS.from_epsg(3857)
    dst_tf, dst_w, dst_h = calculate_default_transform(
        src_crs, dst_crs,
        ds["data"].shape[-1], ds["data"].shape[-2],
        left=x.min() - xres / 2, bottom=y.min() - yres / 2,
        right=x.max() + xres / 2, top=y.max() + yres / 2,
    )
    left_3857, bottom_3857, right_3857, top_3857 = transform_bounds(
        "EPSG:4326", "EPSG:3857", LEFT, BOTTOM, RIGHT, TOP
    )
    wgs84_bounds_tag = f"{LEFT},{BOTTOM},{RIGHT},{TOP}"
    geos_left, geos_bottom, geos_right, geos_top = transform_bounds("EPSG:4326", src_crs, LEFT, BOTTOM, RIGHT, TOP)
    inv_tf = ~src_tf
    col_min, row_max = inv_tf * (geos_left, geos_bottom)
    col_max, row_min = inv_tf * (geos_right, geos_top)
    r0, r1 = max(0, int(row_min)), min(len(y), int(row_max))
    c0, c1 = max(0, int(col_min)), min(len(x), int(col_max))

    # Filter to last 4 hours
    cutoff = np.datetime64("now") - np.timedelta64(BACKFILL_HOURS, "h")
    all_times = ds.time.values
    window_times = [(i, t) for i, t in enumerate(all_times) if t >= cutoff]
    log.info("Processing %d timestamps in last %d hours", len(window_times), BACKFILL_HOURS)

    channels = list(ds.channel.values)
    total = len(window_times) * len(channels)
    uploaded, skipped, failed = 0, 0, 0

    for t_idx, t in window_times:
        ts_str = str(t)[:19].replace("-", "").replace("T", "_").replace(":", "")

        for channel in channels:
            key = f"layers/{channel}/{ts_str}.tif"

            if s3_client.object_exists(s3_bucket, key):
                skipped += 1
                continue

            try:
                chan_idx = list(ds.channel.values).index(channel)
                arr = ds["data"].isel(time=t_idx, channel=chan_idx).values.astype(np.float32)
                arr = np.flipud(arr)

                if channel in SOLAR_NOISE_CONFIG:
                    config = SOLAR_NOISE_CONFIG[channel]  
                    uk_subwindow = arr[r0:r1, c0:c1]
                    valid_local = uk_subwindow[~np.isnan(uk_subwindow)]
                    
                    if len(valid_local) > 0:
                        p_val = float(np.percentile(valid_local, config["percentile"]))
                        
                        # Cliff logic switch: if noise distribution crosses the limit, 
                        # true daylight is active -> Drop threshold to 0.0
                        applied_threshold = 0.0 if p_val > config["cliff_limit"] else p_val
                        
                        # Flatten noise floor cleanly to uniform black
                        if applied_threshold > 0.0:
                            arr = np.where(arr < applied_threshold, 0.0, arr)

                dst_arr = np.full((dst_h, dst_w), np.nan, dtype=np.float32)
                reproject(
                    source=arr, destination=dst_arr,
                    src_transform=src_tf, src_crs=src_crs,
                    dst_transform=dst_tf, dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                    src_nodata=np.nan, dst_nodata=np.nan,
                )

                full_buf = io.BytesIO()
                with rasterio.open(
                    full_buf, "w", driver="GTiff",
                    height=dst_h, width=dst_w,
                    count=1, dtype="float32",
                    crs=dst_crs, transform=dst_tf,
                    nodata=np.nan, compress="deflate", tiled=True,
                ) as tmp:
                    tmp.write(dst_arr, 1)

                full_buf.seek(0)
                crop_buf = io.BytesIO()
                with rasterio.open(full_buf) as src:
                    window = window_from_bounds(left_3857, bottom_3857, right_3857, top_3857, src.transform)
                    cropped = src.read(1, window=window)
                    crop_transform = src.window_transform(window)
                    with rasterio.open(
                        crop_buf, "w", driver="GTiff",
                        height=cropped.shape[0], width=cropped.shape[1],
                        count=1, dtype="float32",
                        crs="EPSG:3857", transform=crop_transform,
                        compress="deflate", tiled=True, nodata=np.nan,
                    ) as dst:
                        dst.write(cropped, 1)
                        dst.update_tags(
                            channel=channel,
                            timestamp=ts_str,
                            bounds_wgs84=wgs84_bounds_tag,
                        )

                s3_client.upload_bytes(s3_bucket, key, crop_buf.getvalue())

                uploaded += 1
                log.info("Uploaded %s @ %s (%d/%d)", channel, ts_str, uploaded + skipped, total)

            except Exception as e:
                failed += 1
                log.exception("Failed %s @ %s: %s", channel, ts_str, e)
                sentry_sdk.capture_exception(e)


    latest_t = all_times[-1]
    latest_ts = str(latest_t)[:19].replace("-", "").replace("T", "_").replace(":", "")
    log.info(
        "Ingest complete: %d uploaded, %d skipped, %d failed (latest=%s)",
        uploaded, skipped, failed, latest_ts,
    )
    return str(latest_t), latest_ts
