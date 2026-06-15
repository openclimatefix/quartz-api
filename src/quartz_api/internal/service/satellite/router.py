"""Historic satellite data router."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from starlette import status

from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.s3 import S3Client, get_geotiff_bucket, get_s3_client

from ._ingest import run_ingest
from .endpoint_types import HistoricSatelliteData, IngestResponse

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
def get_historic_satellite_data_url(
    channel: str,
    timestamp: datetime,
    s3_client: S3ClientDep,
    _: AuthDependency,
) -> HistoricSatelliteData:
    """Get a pre-signed URL for a historic satellite data file."""
    if channel not in VALID_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid channel. Must be one of {sorted(VALID_CHANNELS)}",
        )

    bucket = get_geotiff_bucket()
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
) -> IngestResponse:
    """Trigger ingest of latest satellite data for all channels."""
    if "ocf:admin" not in auth.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    background_tasks.add_task(run_ingest)
    return IngestResponse(
        message="Ingest started for all channels",
        timestamp="processing in background",
    )
