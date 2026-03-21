"""Defines the domain models for the application."""

import datetime as dt
from enum import StrEnum
from typing import Annotated
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import Depends, HTTPException, Query
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field

MAX_WINDOW_DAYS = 7


def convert_to_camelcase(snake_str: str) -> str:
    """Converts a given snake_case string into camelCase."""
    first, *others = snake_str.split("_")
    return "".join([first.lower(), *map(str.title, others)])


# Custom type that gets UTC datetime from timezone-aware input
UTCDatetime = Annotated[
    AwareDatetime,
    AfterValidator(lambda v: v.astimezone(dt.UTC)),
]

UTCDatetimeDefaultWindowStart = Annotated[
    UTCDatetime,
    Query(
        default_factory=lambda: (
            pd.Timestamp.utcnow().floor("6h").to_pydatetime() - dt.timedelta(days=2)
        ),
    ),
]

UTCDatetimeDefaultNowWindowStart = Annotated[
    UTCDatetime,
    Query(
        default_factory=lambda: (
            pd.Timestamp.utcnow().floor("30min").to_pydatetime()
        ),
    ),
]

UTCDatetimeDefaultWindowEnd = Annotated[
    UTCDatetime,
    Query(
        default_factory=lambda: (
            pd.Timestamp.utcnow().floor("6h").to_pydatetime() + dt.timedelta(days=2)
        ),
    ),
]


def validate_window_size(
    start_datetime_utc: UTCDatetimeDefaultWindowStart,
    end_datetime_utc: UTCDatetimeDefaultWindowEnd,
) -> None:
    """Validate that the requested time window does not exceed MAX_WINDOW_DAYS."""
    window = end_datetime_utc - start_datetime_utc
    if window <= dt.timedelta(0):
        raise HTTPException(
            status_code=422,
            detail="start_datetime_utc must be before end_datetime_utc.",
        )
    if window > dt.timedelta(days=MAX_WINDOW_DAYS):
        window_hours = window.total_seconds() / 3600
        raise HTTPException(
            status_code=422,
            detail=(
                f"Requested time window of {window_hours:.0f} hours exceeds "
                f"the maximum allowed window of {MAX_WINDOW_DAYS * 24} hours ({MAX_WINDOW_DAYS} days)."
            ),
        )


WindowSizeValidator = Annotated[None, Depends(validate_window_size)]


class ForecastHorizon(StrEnum):
    """Defines the forecast horizon options.

    Can either be
    - latest: Gets the latest forecast values.
    - horizon: Gets the forecast values for a specific horizon.
    - day_ahead: Gets the day ahead forecast values.
    """

    latest = "latest"
    horizon = "horizon"
    day_ahead = "day_ahead"


def get_timezone() -> ZoneInfo:
    """Stub function for timezone dependency.

    Note: This should be overidden in the router to provide the actual timezone.
    """
    return ZoneInfo(key="UTC")


TZDependency = Annotated[ZoneInfo, Depends(get_timezone)]


class Forecast(BaseModel):
    """Forecast model, this does not provide forecast values."""

    created_time: dt.datetime = Field(
        ...,
        description="The timestamp when the forecast was created",
    )
    name: str = Field(..., description="The name of the forecast model")
    version: str = Field(..., description="The version of the forecast model")
