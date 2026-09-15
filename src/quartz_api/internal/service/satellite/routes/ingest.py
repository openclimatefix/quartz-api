"""Ingest trigger route — kicks off ingest, then re-warms the presign cache."""

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException
from starlette import status
from starlette.responses import Response

from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.s3 import get_s3_client

from .._ingest import _ingest_running, run_ingest
from ..cache import warm_all_satellite_caches
from ..config import VALID_CHANNELS

router = APIRouter()


async def _run_ingest_and_warm(sat_type: str = "rss") -> None:
    """Wrapper function to run ingest and warm the presign cache.

    Run ingest then warm the presign cache for all channels.
    """
    await asyncio.to_thread(run_ingest, sat_type)
    s3_client = get_s3_client()
    await warm_all_satellite_caches(s3_client, VALID_CHANNELS, delay=0)


@router.post(
    "/ingest",
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_ingest(
    background_tasks: BackgroundTasks,
    auth: AuthDependency,
    sat_type: str = "rss",
) -> Response:
    """Trigger ingest of latest satellite data for all channels.

    This endpoint also re-warms the presign cache for all channels after ingest is complete.
    see the _run_ingest_and_warm() function for details.
    """
    if "ocf:admin" not in auth.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if sat_type not in ("rss", "0deg"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid sat_type. Must be either 'rss' or '0deg'",
    )

    #skip any ingest if one is already running, to avoid race conditions
    if _ingest_running:
        return Response(status_code=202, content="Ingest already in progress")

    background_tasks.add_task(_run_ingest_and_warm, sat_type)
    return Response(
        status_code=202,
        content=f"Ingest started for all channels (sat_type={sat_type})",
    )
