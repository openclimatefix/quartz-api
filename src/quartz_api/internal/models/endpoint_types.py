"""Defines the domain models for the application."""

import datetime as dt
from enum import StrEnum
from typing import Annotated
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import Depends, Query
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field


def convert_to_camelcase(snake_str: str) -> str:
    """Converts a given snake_case string into camelCase."""
    first, *others = snake_str.split("_")
    return "".join([first.lower(), *map(str.title, others)])


# Custom type that gets UTC datetime from timezone-aware input
UTCDatetime = Annotated[
    AwareDatetime,
    AfterValidator(lambda v: v.astimezone(dt.UTC)),
]

UTCDatetimeNonAware = Annotated[
    dt.datetime,
    AfterValidator(lambda v: v.replace(tzinfo=dt.UTC)),
]

UTCDatetimeDefaultWindowStart = Annotated[
    UTCDatetime,
    Query(
        default_factory=lambda: (
            pd.Timestamp.utcnow().floor("6h").to_pydatetime() - dt.timedelta(days=2)
        ),
    ),
]

def default_now_window_start() -> dt.datetime:
    """Default factory for UTCDatetimeDefaultNowWindowStart: UTC now floored to 30 minutes."""
    return pd.Timestamp.utcnow().ceil("30min").to_pydatetime()

def default_window_end() -> dt.datetime:
    """Default factory for UTCDatetimeDefaultNowWindowEnd: UTC now floored to 6 hours + 2 days."""
    return pd.Timestamp.utcnow().floor("6h").to_pydatetime() + dt.timedelta(days=2)


UTCDatetimeDefaultNowWindowStart = Annotated[
    UTCDatetime,
    Query(default_factory=default_now_window_start),
]

UTCDatetimeDefaultWindowEnd = Annotated[
    UTCDatetime,
    Query(default_factory=default_window_end),
]

UTCDatetimeDefaultWindowEndNonAware = Annotated[
    UTCDatetimeNonAware,
    Query(default_factory=default_window_end),
]


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
