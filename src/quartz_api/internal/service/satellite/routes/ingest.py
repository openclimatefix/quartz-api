"""Ingest trigger route - kicks off ingest (which also refreshes the rolling stacks)."""

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException
from starlette import status
from starlette.responses import Response

from quartz_api.internal.middleware.auth import AuthDependency

from ..helpers import ingest

router = APIRouter()


@router.post(
    "/ingest",
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_ingest(
    background_tasks: BackgroundTasks,
    auth: AuthDependency,
    sat_type: str = "rss",
) -> Response:
    """Trigger ingest of latest satellite data for all channels, then refresh the stacks."""
    if "ocf:admin" not in auth.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if sat_type not in ("rss", "0deg"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid sat_type. Must be either 'rss' or '0deg'",
    )

    #skip any ingest if one is already running, to avoid race conditions
    if ingest.ingest_lock.locked():
        return Response(status_code=202, content="Ingest already in progress")

    background_tasks.add_task(asyncio.to_thread, ingest.run_ingest, sat_type)
    return Response(
        status_code=202,
        content=f"Ingest started for all channels (sat_type={sat_type})",
    )
