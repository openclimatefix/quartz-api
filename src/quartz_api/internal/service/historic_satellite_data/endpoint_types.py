"""
Historic satellite data endpoint types
"""

from pydantic import BaseModel, Field

class HistoricSatelliteData(BaseModel):
    """
    Historic satellite data
    """

    url: str = Field(..., description="Pre-signed URL for the historic satellite data file")
