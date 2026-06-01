"""
Historic satellite data router
"""

from fastapi import APIRouter, Depends, HTTPException
from .endpoint_types import HistoricSatelliteData
from quartz_api.internal.s3 import S3Client, get_s3_client
from datetime import datetime, timezone

router = APIRouter(
    prefix="/historic-satellite-data",
    tags=["Historic Satellite Data"],
)

VALID_CHANNELS = [
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
]

@router.get("/", response_model=HistoricSatelliteData)
def get_historic_satellite_data_url(
    channel: str,
    timestamp: datetime,
    s3_client: S3Client = Depends(get_s3_client),
):
    """
    Get a pre-signed URL for a historic satellite data file.
    """
    if channel not in VALID_CHANNELS:
        raise HTTPException(status_code=400, detail=f"Invalid channel. Must be one of {VALID_CHANNELS}")

    bucket = "historical-cloud-data-geotiff"
    key = f"layers/{channel}/{timestamp.strftime('%Y%m%d_%H%M%S')}.tif"

    if not s3_client.object_exists(bucket, key):
        raise HTTPException(status_code=404, detail="File not found for the given channel and timestamp")

    return HistoricSatelliteData(
        url=s3_client.get_presigned_url(
            bucket, key
        )
    )
