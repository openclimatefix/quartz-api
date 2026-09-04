"""Historic satellite data endpoint types."""
from datetime import datetime

from pydantic import BaseModel, Field


class HistoricSatelliteData(BaseModel):
    """Historic satellite data."""

    url: str = Field(..., description="Pre-signed URL for the historic satellite data file")


class HistoricSatelliteDataEntry(BaseModel):
    """A single timestamped pre-signed URL."""

    timestamp: datetime = Field(..., description="UTC timestamp of the satellite file")
    url: str = Field(..., description="Pre-signed URL for the historic satellite data file")
