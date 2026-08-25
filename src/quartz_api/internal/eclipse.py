"""Manual eclipse adjustment for national solar forecasts, 12 August 2026.

PVNet has never seen an eclipse in training and ignores the effect, so all models
over-forecast through the eclipse window. Same problem and same fix as 29 March 2025
(openclimatefix/uk-pv-national-gsp-api#404). National only, GB and NL, forecasts only.

TEMPORARY: once the stored values are corrected, set ECLIPSE_ENABLED = False and
delete this module and its call sites.
"""

import dataclasses
import datetime as dt
import logging
import os

from quartz_api.internal import models

log = logging.getLogger(__name__)

ECLIPSE_ENABLED = True

# Override with ECLIPSE_DATE=2026-08-10 to test on an ordinary day.
ECLIPSE_DATE: dt.date = dt.date.fromisoformat(
    os.environ.get("ECLIPSE_DATE", "2026-08-12"),
)

# Fraction of the theoretical non-eclipse value by UTC time of day, from
# James' and Sukh's output tables in our shared doc. GB is half-hourly, NL quarter-hourly.
_FACTORS: dict[str, dict[dt.time, float]] = {
    "GB": {
        dt.time(17, 0): 1.000000,
        dt.time(17, 30): 0.968392,
        dt.time(18, 0): 0.566288,
        dt.time(18, 30): 0.197790,
        dt.time(19, 0): 0.719758,
        dt.time(19, 30): 0.997780,
        dt.time(20, 0): 1.000000,
    },
    "NL": {
        dt.time(17, 0): 1.000000,
        dt.time(17, 15): 1.000000,
        dt.time(17, 30): 0.943559,
        dt.time(17, 45): 0.720819,
        dt.time(18, 0): 0.420696,
        dt.time(18, 15): 0.161150,
        dt.time(18, 30): 0.299477,
        dt.time(18, 45): 0.620353,
        dt.time(19, 0): 0.892493,
        dt.time(19, 15): 0.999197,
        dt.time(19, 30): 1.000000,
        dt.time(19, 45): 1.000000,
        dt.time(20, 0): 1.000000,
    },
}

_STEP_MINUTES: dict[str, int] = {"GB": 30, "NL": 15}


def factor_for(country: str, timestamp: dt.datetime) -> float:
    """Return the eclipse multiplier for a target time, or 1.0 if unaffected."""
    if not ECLIPSE_ENABLED:
        return 1.0

    table = _FACTORS.get(country.upper())
    if not table:
        return 1.0

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.UTC)
    else:
        timestamp = timestamp.astimezone(dt.UTC)

    if timestamp.date() != ECLIPSE_DATE:
        return 1.0

    time_of_day = timestamp.time().replace(second=0, microsecond=0)
    factor = table.get(time_of_day)
    if factor is not None:
        return factor

    # A miss inside the window means serving an unadjusted forecast — be loud.
    if min(table) <= time_of_day <= max(table):
        log.warning(
            "Eclipse adjustment: %s timestamp %s falls inside the eclipse window "
            "but not on the %d-minute table grid — serving it unadjusted",
            country.upper(),
            timestamp.isoformat(),
            _STEP_MINUTES.get(country.upper(), 30),
        )
    return 1.0


def snapshot_factor_for(country: str, timestamp: dt.datetime) -> float:
    """Return the multiplier for a snapshot time, snapped onto the table grid first."""
    if not ECLIPSE_ENABLED or country.upper() not in _FACTORS:
        return 1.0

    step = dt.timedelta(minutes=_STEP_MINUTES[country.upper()])
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.UTC)
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    return factor_for(country, epoch + round((timestamp - epoch) / step) * step)


def _scaled(
    value: models.PredictedGenerationValue,
    factor: float,
) -> models.PredictedGenerationValue:
    """Return a copy with power and every p-level scaled."""
    if factor == 1.0:
        return value
    return dataclasses.replace(
        value,
        power_kilowatts=value.power_kilowatts * factor,
        plevels_kilowatts={
            level: power * factor for level, power in value.plevels_kilowatts.items()
        },
    )


def adjust_predicted_generation(
    values: list[models.PredictedGenerationValue],
    country: str,
) -> list[models.PredictedGenerationValue]:
    """Apply the eclipse adjustment to a national forecast time series."""
    return [_scaled(v, factor_for(country, v.valid_timestamp)) for v in values]


def adjust_snapshot(
    values: list[models.PredictedGenerationValue],
    country: str,
) -> list[models.PredictedGenerationValue]:
    """Apply the eclipse adjustment to a national forecast snapshot."""
    return [_scaled(v, snapshot_factor_for(country, v.valid_timestamp)) for v in values]


def adjust_national_only(
    values: list[models.PredictedGenerationValue],
    country: str,
    national_uuid: object,
) -> list[models.PredictedGenerationValue]:
    """Adjust only the national location's values within a mixed-location list."""
    return [
        _scaled(v, factor_for(country, v.valid_timestamp))
        if v.location_uuid == national_uuid
        else v
        for v in values
    ]
