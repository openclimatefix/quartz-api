"""Presigned-URL lookup routes — single timestamp and full cache dump."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi_cache import FastAPICache

from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.middleware.ratelimit import limiter
from quartz_api.internal.s3 import S3Client, get_geotiff_bucket, get_s3_client

from ..cache import _presign_period_base_key
from ..config import BACKFILL_HOURS, VALID_CHANNELS
from ..endpoint_types import HistoricSatelliteData, HistoricSatelliteDataEntry

router = APIRouter()

S3ClientDep = Annotated[S3Client, Depends(get_s3_client)]


async def _get_cached_entries(channel: str) -> dict[str, dict] | None:
    """Return the pre-warmed {timestamp_iso: {url, signed_at}} for a channel.

    ``None`` if it's never been warmed.
    """
    backend = FastAPICache.get_backend()
    prefix = FastAPICache.get_prefix()
    base = _presign_period_base_key(prefix, channel)
    raw = await backend.get(f"{base}:entries")
    return json.loads(raw) if raw is not None else None


def _bypass_cache(request: Request) -> bool:
    # if the header includes no-cache, skip the pre-warmed cache and hit S3
    return request.headers.get("cache-control", "").lower() == "no-cache"


@router.get("/", response_model=HistoricSatelliteData)
@limiter.limit("50/second")
async def get_historic_satellite_data_url(
    request: Request,
    channel: str,
    s3_client: S3ClientDep,
    _: AuthDependency,
    timestamp: datetime | None = None,
    latest: bool = False,
) -> HistoricSatelliteData:
    """Get a pre-signed URL for a satellite file.

    latest=true: always live S3 lookup for the most recent file (last 30 min), 404 if none.
    otherwise: check cache first, fall back to a live S3 lookup on a miss.
    """
    if channel not in VALID_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid channel. Must be one of {sorted(VALID_CHANNELS)}",
        )

    if not latest and timestamp is None:
        raise HTTPException(status_code=400, detail="timestamp is required unless latest=true")

    bucket = get_geotiff_bucket()

    # latest=true -> always live S3, skip cache
    if latest:
        key = await asyncio.to_thread(s3_client.get_latest_key, bucket, f"layers/{channel}/")
        if key is None:
            raise HTTPException(
                status_code=404,
                detail="No files found for the given channel in the last 30 minutes",
            )
        url = await asyncio.to_thread(s3_client.get_presigned_url, bucket, key)
        return HistoricSatelliteData(url=url)

    # normalize timestamp to UTC
    timestamp = (
        timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
    )

    # not latest -> try cache first
    if not _bypass_cache(request):
        cached = await _get_cached_entries(channel)
        entry = cached.get(timestamp.isoformat()) if cached else None
        if entry is not None:
            return HistoricSatelliteData(url=entry["url"])

    # cache miss (or bypassed) -> fall back to live S3
    key = f"layers/{channel}/{timestamp.strftime('%Y%m%d_%H%M%S')}.tif"
    if not await asyncio.to_thread(s3_client.object_exists, bucket, key):
        raise HTTPException(
            status_code=404,
            detail="File not found for the given channel and timestamp",
        )

    url = await asyncio.to_thread(s3_client.get_presigned_url, bucket, key)
    return HistoricSatelliteData(url=url)


@router.get("/history", response_model=list[HistoricSatelliteDataEntry])
async def get_historic_satellite_data_history(
    request: Request,  # noqa: ARG001
    channel: str,
    _: AuthDependency,
) -> list[HistoricSatelliteDataEntry]:
    """Dump the pre-warmed cache of presigned URLs for a channel, from the last BACKFILL_HOURS."""
    if channel not in VALID_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid channel. Must be one of {sorted(VALID_CHANNELS)}",
        )

    cutoff = datetime.now(tz=UTC) - timedelta(hours=BACKFILL_HOURS)
    entries = await _get_cached_entries(channel) or {}
    return [
        HistoricSatelliteDataEntry(timestamp=ts, url=e["url"])
        for ts, e in sorted(entries.items())
        if datetime.fromisoformat(ts) >= cutoff
    ]
