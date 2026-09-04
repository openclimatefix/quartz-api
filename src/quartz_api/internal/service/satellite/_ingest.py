"""Ingest latest satellite data for all channels into S3."""
import datetime as dt
import io
import logging

import icechunk
import numpy as np
import rasterio
import sentry_sdk
import xarray as xr
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds
from rasterio.windows import from_bounds as window_from_bounds
from rasterio.windows import transform as window_transform

from quartz_api.internal.s3 import (
    get_geotiff_bucket,
    get_icechunk_bucket,
    get_region,
    get_s3_client,
)

from ._blackout import apply_buffer, sun_times

log = logging.getLogger(__name__)

# Bounding box to crop to UK
LEFT, BOTTOM, RIGHT, TOP = -17.05, 46.49, 11.60, 63.31
# How far back to backfill missing data (in hours)
BACKFILL_HOURS = 48
# Per-channel inversion, and whether to black the channel out while the region is dark.
LAYER_CONFIG = {
    "VIS006": {"blackout": True},
    "VIS008": {"blackout": True},
    "IR_016": {"blackout": True},
    "IR_039": {},
    "IR_087": {"invert": True},
    "IR_097": {"invert": True},
    "IR_108": {"invert": True},
    "IR_120": {"invert": True},
    "IR_134": {"invert": True},
    "WV_062": {"invert": True},
    "WV_073": {"invert": True},
}

COMPOSITE_CONFIG: dict[str, list[str]] = {
    "COMPOSITE_VISIBLE": ["IR_016", "VIS008", "VIS006"],
    "COMPOSITE_INFRARED": ["IR_134", "IR_097", "IR_120", "IR_087", "IR_108"],
    "COMPOSITE_BLUE": ["WV_073", "WV_062"],
}

SAT_MAX_ALPHA = 180
SAT_OPACITY = 0.6


def flatten_channels(channel_arrays: list[np.ndarray]) -> np.ndarray:
    """Sum member channel arrays (bottom-to-top) into one composite grey band.

    Returned as float32 in [0, 1], the same dtype and convention as a leaf
    channel's tif (continuous value, no quantization) — so composite and leaf
    tifs are the exact same format and every viewer treats them identically.
    """
    out_grey = np.zeros_like(channel_arrays[0], dtype=np.float32)
    out_alpha = np.zeros_like(channel_arrays[0], dtype=np.float32)
    for arr in channel_arrays:
        finite = np.isfinite(arr)
        grey = np.zeros(arr.shape, dtype=np.float32)
        if finite.any():
            mn, mx = np.nanmin(arr), np.nanmax(arr)
            if mx > mn:
                grey = np.where(
                    finite, np.clip((arr - mn) / (mx - mn), 0, 1), 0.0,
                ).astype(np.float32)
        alpha = (grey * (SAT_MAX_ALPHA / 255.0) * SAT_OPACITY).astype(np.float32)

        new_alpha = alpha + out_alpha * (1 - alpha)
        with np.errstate(invalid="ignore", divide="ignore"):
            new_grey = np.where(
                new_alpha > 0,
                (grey * alpha + out_grey * out_alpha * (1 - alpha)) / new_alpha,
                0.0,
            )
        out_grey, out_alpha = new_grey.astype(np.float32), new_alpha.astype(np.float32)

    return out_grey


def _write_tif(
    bands: list[np.ndarray],
    dtype: str,
    transform: Affine,
    tags: dict[str, str],
) -> bytes:
    """Encode one or more same-shape bands as an in-memory, tagged GeoTIFF.

    (EPSG:3857). Shared by the leaf-channel and composite upload paths below —
    both write a single float32 band, so they're the same format end to end.
    """
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff",
        height=bands[0].shape[0], width=bands[0].shape[1],
        count=len(bands), dtype=dtype,
        crs="EPSG:3857", transform=transform,
        compress="deflate", tiled=True,
        **({"nodata": np.nan} if dtype == "float32" else {}),
    ) as dst:
        for i, band in enumerate(bands, start=1):
            dst.write(band, i)
        dst.update_tags(**tags)
    return buf.getvalue()


_ingest_running: bool = False


def run_ingest(sat_type: str = "rss") -> tuple[str, str]:
    """Run ingest of latest satellite data for all channels.

    Uploads any missing channel+timestamp combos from the last 4 hours,
    skipping ones already present in S3.

    Args:
        sat_type: Satellite type to ingest, either "rss" or "0deg". Defaults to "rss".

    Returns:
        Tuple of (latest timestamp, ts_str).
    """
    global _ingest_running
    if _ingest_running:
        log.info("Ingest already running, skipping")
        return "", ""
    _ingest_running = True
    try:
        return _run_ingest(sat_type)
    finally:
        _ingest_running = False


def _run_ingest(sat_type: str) -> tuple[str, str]:
    log.info("Ingest started for sat_type=%s", sat_type)

    s3_bucket = get_geotiff_bucket()
    icechunk_bucket = get_icechunk_bucket()
    if sat_type == "0deg":
        icechunk_prefix = "odegree_v1/data/odegree_uk3000m.icechunk"
    else: #default to rss
        icechunk_prefix = "rss_v1/data/rss_uk3000m.icechunk"
    region = get_region()
    s3_client = get_s3_client()

    log.info(
        "Ingest config: geotiff_bucket=%s icechunk_bucket=%s icechunk_prefix=%s region=%s",
        s3_bucket, icechunk_bucket, icechunk_prefix, region,
    )

    repo = icechunk.Repository.open(
        storage=icechunk.s3_storage(
            bucket=icechunk_bucket,
            prefix=icechunk_prefix,
            **({"region": region} if region else {}),
        ),
    )
    ds = xr.open_zarr(repo.readonly_session("main").store)
    log.info(
        "Opened zarr store: time_range=%s..%s channels=%s data_shape=%s",
        ds.time.values.min(), ds.time.values.max(),
        list(ds.channel.values), ds["data"].shape,
    )

    area = ds.attrs["area"]
    proj = area[next(iter(area))]["projection"]
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
        "EPSG:4326", "EPSG:3857", LEFT, BOTTOM, RIGHT, TOP,
    )
    wgs84_bounds_tag = f"{LEFT},{BOTTOM},{RIGHT},{TOP}"
    crop_window = window_from_bounds(left_3857, bottom_3857, right_3857, top_3857, dst_tf)
    crop_transform = window_transform(crop_window, dst_tf)

    # Filter to last 48 hours
    cutoff = np.datetime64("now") - np.timedelta64(BACKFILL_HOURS, "h")
    all_times = ds.time.values
    window_times = [(i, t) for i, t in enumerate(all_times) if t >= cutoff]
    log.info("Processing %d timestamps in last %d hours", len(window_times), BACKFILL_HOURS)

    channels = list(ds.channel.values)
    total = len(window_times) * len(channels)
    uploaded, skipped, failed = 0, 0, 0

    for t_idx, t in window_times:
        ts_str = str(t)[:19].replace("-", "").replace("T", "_").replace(":", "")
        ts_dt = dt.datetime.fromisoformat(str(t)[:19]).replace(tzinfo=dt.UTC)
        sunrise, sunset = apply_buffer(
            *sun_times(ts_dt.date(), (LEFT + RIGHT) / 2, (BOTTOM + TOP) / 2),
        )
        is_dark = not sunrise <= ts_dt < sunset

        for channel in channels:
            key = f"layers/{channel}/{ts_str}.tif"

            if s3_client.object_exists(s3_bucket, key):
                skipped += 1
                continue

            try:
                config = LAYER_CONFIG.get(channel, {})

                if config.get("blackout") and is_dark:
                    dst_arr = np.zeros((dst_h, dst_w), dtype=np.float32)
                else:
                    chan_idx = list(ds.channel.values).index(channel)
                    arr = ds["data"].isel(time=t_idx, channel=chan_idx).values.astype(np.float32)
                    arr = np.flipud(arr)

                    if config.get("invert"):
                        arr = 1.0 - arr

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
                with rasterio.open(full_buf) as src:
                    cropped = src.read(1, window=crop_window)

                tif_bytes = _write_tif(
                    [cropped], "float32", crop_transform,
                    tags={
                        "channel": channel,
                        "timestamp": ts_str,
                        "bounds_wgs84": wgs84_bounds_tag,
                    },
                )
                s3_client.upload_bytes(s3_bucket, key, tif_bytes)
                uploaded += 1
                log.info("Uploaded %s @ %s (%d/%d)", channel, ts_str, uploaded + skipped, total)

            except Exception as e:
                failed += 1
                log.exception("Failed %s @ %s: %s", channel, ts_str, e)
                sentry_sdk.capture_exception(e)

        # Second loop to process composites
        for name, members in COMPOSITE_CONFIG.items():
            composite_key = f"layers/{name}/{ts_str}.tif"
            if s3_client.object_exists(s3_bucket, composite_key):
                continue

            member_keys = [f"layers/{m}/{ts_str}.tif" for m in members]
            if not all(s3_client.object_exists(s3_bucket, k) for k in member_keys):
                log.info("Skipping composite %s @ %s: missing member(s)", name, ts_str)
                continue

            try:
                member_arrays = []
                for k in member_keys:
                    with rasterio.open(io.BytesIO(s3_client.download_bytes(s3_bucket, k))) as src:
                        member_arrays.append(src.read(1))

                grey = flatten_channels(member_arrays)
                tif_bytes = _write_tif(
                    [grey], "float32", crop_transform,
                    tags={"channel": name, "timestamp": ts_str, "bounds_wgs84": wgs84_bounds_tag},
                )
                s3_client.upload_bytes(s3_bucket, composite_key, tif_bytes)
                uploaded += 1
                log.info("Uploaded composite %s @ %s", name, ts_str)

            except Exception as e:
                failed += 1
                log.exception("Failed composite %s @ %s: %s", name, ts_str, e)
                sentry_sdk.capture_exception(e)

    latest_t = all_times[-1]
    latest_ts = str(latest_t)[:19].replace("-", "").replace("T", "_").replace(":", "")
    log.info(
        "Ingest complete: %d uploaded, %d skipped, %d failed (latest=%s)",
        uploaded, skipped, failed, latest_ts,
    )
    return str(latest_t), latest_ts
