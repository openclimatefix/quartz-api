"""Historic satellite data router."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from starlette import status
from starlette.responses import Response

from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.middleware.ratelimit import limiter
from quartz_api.internal.s3 import S3Client, get_geotiff_bucket, get_s3_client

from ._ingest import _ingest_running, run_ingest
from .endpoint_types import HistoricSatelliteData

router = APIRouter(
    prefix="/satellite",
    tags=["Satellite"],
)

VALID_CHANNELS = frozenset(
    {
        "IR_016",
        "IR_039",
        "IR_087",
        "IR_097",
        "IR_108",
        "IR_120",
        "IR_134",
        "VIS006",
        "VIS008",
        "WV_062",
        "WV_073",
    },
)

S3ClientDep = Annotated[S3Client, Depends(get_s3_client)]


@router.get("/", response_model=HistoricSatelliteData)
@limiter.limit("50/second")
def get_historic_satellite_data_url(
    request: Request,  # noqa: ARG001
    channel: str,
    s3_client: S3ClientDep,
    _: AuthDependency,
    timestamp: datetime | None = None,
    latest: bool = False,
) -> HistoricSatelliteData:
    """Get a pre-signed URL for a satellite file; ``latest=true`` ignores ``timestamp``."""
    if channel not in VALID_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid channel. Must be one of {sorted(VALID_CHANNELS)}",
        )

    bucket = get_geotiff_bucket()

    if latest:
        key = s3_client.get_latest_key(bucket, f"layers/{channel}/")
        if key is None:
            raise HTTPException(
                status_code=404,
                detail="No files found for the given channel in the last 30 minutes",
            )
        return HistoricSatelliteData(url=s3_client.get_presigned_url(bucket, key))

    if timestamp is None:
        raise HTTPException(
            status_code=400,
            detail="timestamp is required unless latest=true",
        )

    key = f"layers/{channel}/{timestamp.strftime('%Y%m%d_%H%M%S')}.tif"

    if not s3_client.object_exists(bucket, key):
        raise HTTPException(
            status_code=404,
            detail="File not found for the given channel and timestamp",
        )

    return HistoricSatelliteData(url=s3_client.get_presigned_url(bucket, key))


@router.post(
    "/ingest",
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_ingest(
    background_tasks: BackgroundTasks,
    auth: AuthDependency,
) -> Response:
    """Trigger ingest of latest satellite data for all channels."""
    if "ocf:admin" not in auth.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if _ingest_running:
        return Response(status_code=202, content="Ingest already in progress")
    background_tasks.add_task(run_ingest)
    return Response(status_code=202, content="Ingest started for all channels")
