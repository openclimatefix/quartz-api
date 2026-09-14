"""Cache warming logic for the satellite API."""

import asyncio
import datetime as dt
import json
import logging

from fastapi_cache import FastAPICache

from quartz_api.internal.s3 import S3Client, get_geotiff_bucket

from .config import BACKFILL_HOURS, REFRESH_MARGIN_SECS

log = logging.getLogger(__name__)

# below flag is used to avoid warming the same channel concurrently
satellite_cache_warming: dict[str, bool] = {}


def _presign_period_base_key(prefix: str, channel: str) -> str:
    """Return the base cache key for the pre-warmed presigned-URL cache."""
    return f"{prefix}:satellite:period:{channel}"


async def warm_satellite_channel_cache(s3_client: S3Client, channel: str) -> None:
    """Pre-generate and cache presigned URLs for one channel.

    Covers the last ``BACKFILL_HOURS`` in 15-minute steps, as a rolling
    window: entries from a previous warm that now fall outside that window are
    dropped, not carried forward. Incremental within the window: reuses any
    already-cached entry that isn't close to expiring instead of re-checking S3 for
    it, so a warm triggered after every ingest run only pays for the timestamps
    that are new (just ingested) or missing from the previous run — not the whole
    window every time.

    S3 calls (``object_exists`` / ``get_presigned_url``) are synchronous, so they're
    run via ``asyncio.to_thread`` to avoid blocking the event loop and starving live
    requests while a channel warms.
    """
    if satellite_cache_warming.get(channel):
        log.info("satellite presign cache warm skipped (already in progress): %s", channel)
        return

    satellite_cache_warming[channel] = True
    log.info("satellite presign cache warming: %s", channel)
    try:
        bucket = get_geotiff_bucket()
        now = dt.datetime.now(tz=dt.UTC)
        end = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
        start = end - dt.timedelta(hours=BACKFILL_HOURS)

        backend = FastAPICache.get_backend()
        prefix = FastAPICache.get_prefix()
        base = _presign_period_base_key(prefix, channel)

        raw_existing = await backend.get(f"{base}:entries")
        existing: dict[str, dict] = json.loads(raw_existing) if raw_existing else {}

        # refresh any existing entry that was signed too long ago, to avoid
        # returning a URL that will expire soon
        min_signed_at = now - dt.timedelta(
            seconds=BACKFILL_HOURS * 60 * 60 - REFRESH_MARGIN_SECS,
        )

        entries: dict[str, dict] = {}
        ts = start
        while ts <= end:
            iso = ts.isoformat()
            cached = existing.get(iso)
            signed_at = (
                dt.datetime.fromisoformat(cached["signed_at"]) if cached is not None else None
            )
            if signed_at is not None and signed_at > min_signed_at:
                entries[iso] = cached
                ts += dt.timedelta(minutes=15)
                continue

            key = f"layers/{channel}/{ts:%Y%m%d_%H%M%S}.tif"
            if await asyncio.to_thread(s3_client.object_exists, bucket, key):
                url = await asyncio.to_thread(
                    s3_client.get_presigned_url, bucket, key, BACKFILL_HOURS * 60 * 60,
                )
                entries[iso] = {"url": url, "signed_at": now.isoformat()}
            ts += dt.timedelta(minutes=15)

        await backend.set(
            f"{base}:entries", json.dumps(entries), expire=BACKFILL_HOURS * 60 * 60,
        )
        await backend.set(
            f"{base}:_meta",
            json.dumps({"cache_updated_utc": now.isoformat(), "count": len(entries)}),
            expire=BACKFILL_HOURS * 60 * 60,
        )
        log.info("satellite presign cache warmed: %s — %d entries", channel, len(entries))
    except Exception:
        log.exception("satellite presign cache warm failed: %s", channel)
    finally:
        satellite_cache_warming[channel] = False


async def warm_all_satellite_caches(
    s3_client: S3Client,
    channels: frozenset[str],
    *,
    delay: float = 5,
) -> None:
    """Warm the presigned-URL cache for every channel, concurrently."""
    await asyncio.sleep(delay)
    results = await asyncio.gather(
        *(warm_satellite_channel_cache(s3_client, channel) for channel in channels),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            log.exception("satellite cache warm subtask failed", exc_info=r)
    log.info("satellite presign cache warming complete: %d channel(s)", len(channels))
