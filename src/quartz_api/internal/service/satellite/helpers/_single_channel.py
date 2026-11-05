"""Read satellite frames from the Icechunk store and turn them into single-channel tifs."""
import datetime as dt
import logging
from dataclasses import dataclass

import icechunk
import numpy as np
import rasterio
import xarray as xr
from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject, transform_bounds

from quartz_api.internal.s3 import S3Client, get_region

from ..config import BOTTOM, ICECHUNK_PREFIX, LAYER_CONFIG, LEFT, RESOLUTION_M, RIGHT, TOP
from .common import DST_CRS, DST_SHAPE, DST_TF, TS_FMT, build_layer_tif, save

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    """One satellite's Icechunk data, cropped to Europe in its native projection."""

    data: xr.DataArray  # (time, channel, y, x), rows north-up
    crs: CRS
    transform: Affine

    def slots(self, cutoff: dt.datetime) -> list[tuple[dt.datetime, int]]:
        """(slot, time index) for every timestamp at or after cutoff, oldest first."""
        times = self.data["time"].values.astype("datetime64[s]")
        return sorted(
            (s, i) for i, t in enumerate(times)
            if (s := t.item().replace(tzinfo=dt.UTC)) >= cutoff
        )


def open_source(bucket: str, sat_type: str) -> Source | None:
    """Open the sat_type Icechunk store; None if nothing has been committed to it yet."""
    prefix = ICECHUNK_PREFIX[sat_type]
    repo = icechunk.Repository.open(
        storage=icechunk.s3_storage(bucket=bucket, prefix=prefix, region=get_region() or None),
    )
    try:
        ds = xr.open_zarr(repo.readonly_session("main").store)
    except FileNotFoundError:  # zarr's GroupNotFoundError: repo created, nothing committed
        log.warning("Icechunk store s3://%s/%s has no data yet", bucket, prefix)
        return None

    proj = next(iter(ds.attrs["area"].values()))["projection"]
    src_crs = CRS.from_proj4(
        f"+proj=geos +lon_0={proj['lon_0']} +h={proj['h']} "
        f"+x_0={proj['x_0']} +y_0={proj['y_0']} "
        f"+a={proj['a']} +rf={proj['rf']}",
    )

    # Crop before reprojecting (full disk's off-earth corners can't go to 3857), with
    # one output pixel of margin so edge pixels average a full footprint.
    x, y = ds["x_geostationary"].values, ds["y_geostationary"].values
    xres, yres = abs(x[1] - x[0]), abs(y[1] - y[0])
    x0, y0, x1, y1 = transform_bounds(
        "EPSG:4326", src_crs, LEFT, BOTTOM, RIGHT, TOP, densify_pts=101,
    )
    m = RESOLUTION_M
    xi = np.flatnonzero((x >= x0 - m) & (x <= x1 + m))
    yi = np.flatnonzero((y >= y0 - m) & (y <= y1 + m))[::-1]  # y ascending -> rows north-up
    data = ds["data"].isel(x_geostationary=xi, y_geostationary=yi).transpose(
        "time", "channel", "y_geostationary", "x_geostationary",
    )
    src_tf = rasterio.transform.from_origin(
        x[xi].min() - xres / 2, y[yi].max() + yres / 2, xres, yres,
    )
    log.info("Opened s3://%s/%s: %d timestamps", bucket, prefix, ds.sizes["time"])
    return Source(data=data, crs=src_crs, transform=src_tf)


def ingest_channels(
    s3: S3Client,
    geo_bucket: str,
    src: Source,
    t_idx: int,
    slot: dt.datetime,
    uploaded: dict[str, set[str]],
) -> set[str]:
    """Reproject each still-missing channel of one timestamp to the Europe grid, and upload."""
    ts = slot.strftime(TS_FMT)
    todo = [ch for ch in LAYER_CONFIG if f"{ts}.tif" not in uploaded[ch]]
    if not todo:
        return set()

    # One read per timestamp: each chunk holds every channel.
    frame = src.data.isel(time=t_idx).sel(channel=todo).values.astype(np.float32)
    for ch, arr in zip(todo, frame, strict=True):
        cfg = LAYER_CONFIG[ch]
        out = np.full(DST_SHAPE, np.nan, dtype=np.float32)
        # average, not bilinear: each output pixel covers several source pixels
        reproject(
            arr, out,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=DST_TF, dst_crs=DST_CRS,
            resampling=Resampling.average, src_nodata=np.nan, dst_nodata=np.nan,
        )
        lo, hi = cfg["range"]
        grey = np.clip((out - lo) / (hi - lo), 0, 1)
        if cfg.get("invert"):
            grey = 1 - grey
        save(s3, geo_bucket, ch, ts, build_layer_tif(ch, grey, timestamp=ts), uploaded)
    return set(todo)
