"""Presigned-URL lookup routes - one timestamp's tif, or a layer's rolling 48 h stack."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.middleware.ratelimit import limiter
from quartz_api.internal.s3 import S3Client, get_geotiff_bucket, get_s3_client

from ..config import VALID_CHANNELS
from ..endpoint_types import HistoricSatelliteData
from ..helpers._stack import stack_url

router = APIRouter()

S3ClientDep = Annotated[S3Client, Depends(get_s3_client)]


def _check_channel(channel: str) -> None:
    if channel not in VALID_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid channel. Must be one of {sorted(VALID_CHANNELS)}",
        )


@router.get("/", response_model=HistoricSatelliteData)
@limiter.limit("50/second")
async def get_historic_satellite_data_url(
    request: Request, # noqa: ARG001
    channel: str,
    s3_client: S3ClientDep,
    _: AuthDependency,
    timestamp: datetime | None = None,
    latest: bool = False,
) -> HistoricSatelliteData:
    """Get a pre-signed URL for one timestamp's satellite file.

    latest=true: the most recent file (last 30 min), 404 if none.
    otherwise: the file for `timestamp`, 404 if it doesn't exist.
    """
    _check_channel(channel)
    if not latest and timestamp is None:
        raise HTTPException(status_code=400, detail="timestamp is required unless latest=true")

    bucket = get_geotiff_bucket()

    if latest:
        key = await asyncio.to_thread(s3_client.get_latest_key, bucket, f"layers/{channel}/")
        if key is None:
            raise HTTPException(
                status_code=404,
                detail="No files found for the given channel in the last 30 minutes",
            )
        url = await asyncio.to_thread(s3_client.get_presigned_url, bucket, key)
        return HistoricSatelliteData(url=url)

    timestamp = (
        timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
    )
    key = f"layers/{channel}/{timestamp.strftime('%Y%m%d_%H%M%S')}.tif"
    if not await asyncio.to_thread(s3_client.object_exists, bucket, key):
        raise HTTPException(
            status_code=404,
            detail="File not found for the given channel and timestamp",
        )

    url = await asyncio.to_thread(s3_client.get_presigned_url, bucket, key)
    return HistoricSatelliteData(url=url)


@router.get("/stack", response_model=HistoricSatelliteData)
@limiter.limit("20/second")
async def get_satellite_stack_url(
    request: Request,  # noqa: ARG001
    channel: str,
    _: AuthDependency,
) -> HistoricSatelliteData:
    """Get a pre-signed URL for a layer's rolling stack: one multi-band tif of the last 48 h."""
    _check_channel(channel)
    url = await asyncio.to_thread(stack_url, channel)
    if url is None:
        raise HTTPException(status_code=404, detail="No rolling stack for this channel yet")
    return HistoricSatelliteData(url=url)
