"""Ingest latest satellite data for all channels into S3."""
import io
import logging

import icechunk
import numpy as np
import rasterio
import s3fs
import xarray as xr
from rasterio.crs import CRS
from rasterio.warp import Resampling, calculate_default_transform, reproject
from rasterio.windows import from_bounds as window_from_bounds

log = logging.getLogger(__name__)

LEFT, BOTTOM, RIGHT, TOP = -20.0, 45.8, 15.0, 64.2
S3_BUCKET = "historical-cloud-data-geotiff"
BACKFILL_HOURS = 4


def run_ingest() -> tuple[str, str]:
    """Run ingest of latest satellite data for all channels.

    Uploads any missing channel+timestamp combos from the last 48 hours,
    skipping ones already present in S3.

    Returns:
        Tuple of (latest timestamp, ts_str).
    """
    log.info("Ingest started")

    fs = s3fs.S3FileSystem(client_kwargs={"region_name": "eu-west-1"})

    repo = icechunk.Repository.open(
        storage=icechunk.s3_storage(
            bucket="nowcasting-sat-development",
            prefix="odegree_v1/data/odegree_uk3000m.icechunk",
            region="eu-west-1",
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

    dst_crs = CRS.from_epsg(4326)
    dst_tf, dst_w, dst_h = calculate_default_transform(
        src_crs, dst_crs,
        ds["data"].shape[-1], ds["data"].shape[-2],
        left=x.min() - xres / 2, bottom=y.min() - yres / 2,
        right=x.max() + xres / 2, top=y.max() + yres / 2,
    )

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

        for channel in channels:
            s3_path = f"s3://{S3_BUCKET}/layers/{channel}/{ts_str}.tif"

            if fs.exists(s3_path):
                skipped += 1
                continue

            try:
                chan_idx = list(ds.channel.values).index(channel)
                arr = ds["data"].isel(time=t_idx, channel=chan_idx).values.astype(np.float32)
                arr = np.flipud(arr)

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
                    window = window_from_bounds(LEFT, BOTTOM, RIGHT, TOP, src.transform)
                    cropped = src.read(1, window=window)
                    crop_transform = src.window_transform(window)
                    with rasterio.open(
                        crop_buf, "w", driver="GTiff",
                        height=cropped.shape[0], width=cropped.shape[1],
                        count=1, dtype="float32",
                        crs="EPSG:4326", transform=crop_transform,
                        compress="deflate", tiled=True, nodata=np.nan,
                    ) as dst:
                        dst.write(cropped, 1)
                        dst.update_tags(channel=channel, timestamp=ts_str)

                crop_buf.seek(0)
                with fs.open(s3_path, "wb") as f:
                    f.write(crop_buf.read())

                uploaded += 1
                log.info("Uploaded %s @ %s (%d/%d)", channel, ts_str, uploaded + skipped, total)

            except Exception as e:
                failed += 1
                log.exception("Failed %s @ %s: %s", channel, ts_str, e)

    latest_t = all_times[-1]
    latest_ts = str(latest_t)[:19].replace("-", "").replace("T", "_").replace(":", "")
    log.info(
        "Ingest complete: %d uploaded, %d skipped, %d failed (latest=%s)",
        uploaded, skipped, failed, latest_ts,
    )
    return str(latest_t), latest_ts
