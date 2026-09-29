"""Orchestrate ingest: raw files -> per-channel tifs -> composites -> rolling stacks."""
import datetime as dt
import logging
from threading import Lock

import sentry_sdk

from quartz_api.internal.s3 import (
    get_geotiff_bucket,
    get_icechunk_bucket,
    get_s3_client,
)

from ..config import BACKFILL_HOURS, LAYER_CONFIG
from . import _composite, _single_channel
from ._stack import rebuild_stacks
from .common import LAYERS, TS_FMT

log = logging.getLogger(__name__)

ingest_lock = Lock()  # one ingest at a time


def _find_missing_timestamps(
    keys: list[str],
    backfill_hours: int,
    interval: dt.timedelta = dt.timedelta(minutes=15),
) -> list[dt.datetime]:
    """Interval-aligned timestamps missing within the last backfill_hours (from keys' basenames)."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=backfill_hours)
    interval_minutes = int(interval.total_seconds() // 60)

    existing_timestamps = set()
    for k in keys:
        if k.endswith(".tif"):
            ts = dt.datetime.strptime(
                k.split("/")[-1].removesuffix(".tif"), TS_FMT,
            ).replace(tzinfo=dt.UTC)
            if ts >= cutoff and ts.minute % interval_minutes == 0 and ts.second == 0:
                existing_timestamps.add(ts)

    if not existing_timestamps:
        return []

    expected = min(existing_timestamps)
    #this end helps checking for stale data, with 30min buffer
    end = max(max(existing_timestamps), dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30))
    missing = []
    while expected <= end:
        if expected not in existing_timestamps:
            missing.append(expected)
        expected += interval
    return missing


def run_ingest(sat_type: str = "rss") -> tuple[str, str]:
    """Upload missing layer tifs for the last BACKFILL_HOURS (sat_type rss/0deg), then stacks."""
    if not ingest_lock.acquire(blocking=False):
        log.info("Ingest already running, skipping")
        return "", ""
    try:
        s3, bucket = get_s3_client(), get_geotiff_bucket()
        uploaded = {
            layer: {k.rsplit("/", 1)[-1] for k in s3.list_keys(bucket, f"layers/{layer}/")}
            for layer in LAYERS
        }
        n_before = sum(map(len, uploaded.values()))
        latest = _run_ingest(sat_type, uploaded)
        log.info("Ingest uploaded %d tif(s)", sum(map(len, uploaded.values())) - n_before)
        rebuild_stacks(s3, bucket)
        return latest
    finally:
        ingest_lock.release()


def _run_ingest(
    sat_type: str, uploaded: dict[str, set[str]], check_gaps: bool = True,
) -> tuple[str, str]:
    geo_bucket, icechunk_bucket, s3 = get_geotiff_bucket(), get_icechunk_bucket(), get_s3_client()
    channels = list(LAYER_CONFIG)
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=BACKFILL_HOURS)

    raw_files = _single_channel.list_raw_files(s3, icechunk_bucket, sat_type, cutoff)
    for slot, key in raw_files:
        ts = slot.strftime(TS_FMT)
        try:
            _single_channel.ingest_channels(s3, icechunk_bucket, geo_bucket, key, slot, uploaded)
            _composite.build_composites(s3, geo_bucket, ts, uploaded)
        except Exception as e:
            log.exception("Failed %s (slot %s): %s", key, ts, e)
            sentry_sdk.capture_exception(e)

    if check_gaps and any(
        _find_missing_timestamps(list(uploaded[ch]), BACKFILL_HOURS) for ch in channels
    ):
        other = "0deg" if sat_type == "rss" else "rss"
        log.warning("Gaps found, retrying ingest with sat_type=%s", other)
        _run_ingest(other, uploaded, check_gaps=False)

        still_missing = [
            f"{ch}@{t:{TS_FMT}}"
            for ch in channels
            for t in _find_missing_timestamps(list(uploaded[ch]), BACKFILL_HOURS)
        ]
        if still_missing:
            log.error("Missing tiles after fallback to %s: %s", other, still_missing)
            sentry_sdk.capture_message(
                "Missing satellite tiles after fallback ingest",
                level="error",
                fingerprint=["missing-satellite-tiles"],
                tags={"sat_type": sat_type, "fallback_sat_type": other},
                extras={"missing_tiles": still_missing},
            )

    latest = raw_files[-1][0] if raw_files else None
    log.info("Ingest %s done, latest slot %s", sat_type, latest)
    return (latest.isoformat(), latest.strftime(TS_FMT)) if latest else ("", "")
