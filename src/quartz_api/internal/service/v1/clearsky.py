"""Clear-sky generation estimate."""

import datetime as dt

import pandas as pd
from fastapi import HTTPException
from pvlib import inverter, irradiance, location, pvsystem
from starlette import status

from quartz_api.internal import models

from .endpoint_types import SolarMetadata
from .helpers import validate_window


def clearsky_times(start: dt.datetime | None, end: dt.datetime | None) -> list[dt.datetime]:
    """15-minute times for the window, defaulting to now (floored) → +48h."""
    now = pd.Timestamp.now(tz="UTC").floor("15min").to_pydatetime()
    start, end = start or now, end or now + dt.timedelta(hours=48)
    validate_window(start, end)
    return list(pd.date_range(start, end, freq="15min", inclusive="left").to_pydatetime())


def site_clearsky_kw(site: models.Location, times: list[dt.datetime]) -> list[float]:
    """AC power in kW the site would make under a clear sky at each time."""
    solar = SolarMetadata.model_construct(**site.metadata)
    missing = [
        f for f in ("module_capacity_kW", "inverter_capacity_kW") if getattr(solar, f) is None
    ]

    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Site '{site.uuid}' needs {' and '.join(missing)} set for clearsky.",
        )

    index = pd.DatetimeIndex(times)
    loc = location.Location(site.latitude, site.longitude)
    sky, sun = loc.get_clearsky(index), loc.get_solarposition(index)
    poa = irradiance.get_total_irradiance(
        surface_tilt=solar.tilt or 0.0,
        surface_azimuth=180.0 if solar.orientation is None else solar.orientation,
        solar_zenith=sun["apparent_zenith"],
        solar_azimuth=sun["azimuth"],
        dni=sky["dni"],
        ghi=sky["ghi"],
        dhi=sky["dhi"],
    )["poa_global"]

    # PVWatts V1: cells at 25°C, -0.005 temperature coefficient.
    pdc = pvsystem.pvwatts_dc(poa, 25.0, solar.module_capacity_kW, -0.005)
    pac = inverter.pvwatts(pdc, solar.inverter_capacity_kW)
    return pac.fillna(0.0).clip(lower=0.0).tolist()
