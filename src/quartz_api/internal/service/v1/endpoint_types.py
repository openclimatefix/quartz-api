"""Pydantic models for v1 API request/response types."""

import datetime as dt
import enum
from typing import Annotated

from fastapi import Path, Query
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    WithJsonSchema,
    field_validator,
)

from quartz_api.constants import SUPPORT_EMAIL
from quartz_api.internal import models

from .country_config import COUNTRIES


def _get_forecast_model_names() -> tuple[str, ...]:
    """Extract all unique forecast model API names (slugs) from country configs."""
    names: set[str] = set()
    for country_cfg in COUNTRIES.values():
        for rt in country_cfg.region_types:
            for m in rt.forecast_models:
                names.add(m.api_name)
    return tuple(sorted(names))


# ValidForecastModel and ValidRegionType are evaluated once at import time so that
# Swagger/Scalar renders a static dropdown of valid values.  The enum spans all
# countries — per-country validation happens inside the route handlers.
ValidForecastModel = Annotated[
    str,
    Query(
        description=(
            "Forecast model name. If omitted, the default model for the region type is used "
            "(see `/{country}/{source}/region-types`)."
        ),
        enum=list(_get_forecast_model_names()),
    ),
]


# Deprecated alias for `model_name`, accepted so existing integrations keep working.
# Deliberately absent from the OpenAPI schema: including it would allow new clients
# to adopt a name we want rid of. We'll drop the param once no users are using it.
# No enum either — retired model slugs resolve through it as well as current ones.
DeprecatedForecastModel = Annotated[
    str | None,
    Query(include_in_schema=False),
]

ValidForecastModelVersion = Annotated[
    str | None,
    Query(
        description=(
            "Forecast model version. If omitted, the latest version of the selected "
            "model is used."
        ),
    ),
]


class DetailLevel(enum.StrEnum):
    """How much per-value metadata a time-series response carries."""

    values = "values"
    runs = "runs"
    full = "full"


ValidDetail = Annotated[
    DetailLevel,
    Query(
        description=(
            "How much metadata to return per value. "
            "`values` (default): the values only. "
            "`runs`: adds which model run produced each value (run and init time, model "
            "name and version) plus that value's own capacity; the top-level fields "
            "describe only the latest run in the response. "
            "`full`: adds the forecaster's own metadata, including when each input data "
            "source was last ingested. Keys there vary by model. "
            "On generation routes there is no model run, so `runs` adds only capacity and "
            "`full` adds nothing further."
        ),
    ),
]


def _get_region_type_names(*, exclude_nation: bool = False) -> tuple[str, ...]:
    """Extract all unique region type slugs from country configs."""
    names: set[str] = set()
    for country_cfg in COUNTRIES.values():
        for rt in country_cfg.region_types:
            if exclude_nation and rt.location_type == models.LocationType.NATION:
                continue
            names.add(rt.type)
    return tuple(sorted(names))


_REGION_TYPE_DESCRIPTION = (
    "Region type slug (e.g. 'gsp', 'national'). "
    "Valid values are country-specific; see `/{country}/{source}/region-types`. "
    "The enum lists all types across all countries."
)

_PERIOD_REGION_TYPE_DESCRIPTION = (
    "Region type slug (e.g. 'gsp'). Only sub-national types are supported; "
    "national-level data is not pre-warmed. "
    "Valid values are country-specific; see `/{country}/{source}/region-types`. "
    "The enum lists all types across all countries."
)

ValidRegionType = Annotated[
    str,
    Query(
        description=_REGION_TYPE_DESCRIPTION,
        enum=list(_get_region_type_names()),
    ),
]

# Optional variant: Annotated wraps str | None so FastAPI propagates the enum
# even when the parameter is not required.
OptionalValidRegionType = Annotated[
    str | None,
    Query(
        description=_REGION_TYPE_DESCRIPTION,
        enum=list(_get_region_type_names()),
    ),
]

# Period endpoints only support sub-national region types (national is never pre-warmed).
ValidPeriodRegionType = Annotated[
    str,
    Query(
        description=_PERIOD_REGION_TYPE_DESCRIPTION,
        enum=list(_get_region_type_names(exclude_nation=True)),
    ),
]


def _get_observer_sources() -> tuple[str, ...]:
    """Extract all unique observer API names from country configs."""
    sources = set()
    for country_cfg in COUNTRIES.values():
        for gen_type in country_cfg.generation_sources:
            sources.add(gen_type.api_name)
    return tuple(sorted(sources))


def _build_observer_pattern() -> str:
    """Build regex pattern from available observer sources."""
    sources = _get_observer_sources()
    if not sources:
        return "^$"  # fallback if empty
    return f"^({'|'.join(sources)})$"


def _parse_source(v: str) -> models.EnergyType:
    if v == "solar":
        return models.EnergyType.SOLAR
    raise ValueError(
        f"Unsupported energy source '{v}'. Currently only 'solar' is available.",
    )


ValidSource = Annotated[
    models.EnergyType,
    BeforeValidator(_parse_source),
    WithJsonSchema({"type": "string", "enum": ["solar"]}),
    Path(
        description="The energy source type. Currently only 'solar' is supported.",
        examples=["solar"],
    ),
]


class _CountryParam(str):
    """Country code path param — a str that proxies its CountryConfig attributes."""

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: object,
        handler: object,
    ) -> object:
        from pydantic_core import core_schema

        def validate(v: object) -> "_CountryParam":
            if isinstance(v, cls):
                return v
            upper = v.upper() if isinstance(v, str) else str(v).upper()  # type: ignore[union-attr]
            if upper not in COUNTRIES:
                raise ValueError(
                    f"Unknown country '{v}'. Supported: {sorted(COUNTRIES.keys())}",
                )
            return cls(upper)

        return core_schema.no_info_plain_validator_function(validate)  # type: ignore[return-value]

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema_obj: object,
        handler: object,
    ) -> dict:
        return {"type": "string", "enum": list(COUNTRIES.keys())}

    def __getattr__(self, name: str) -> object:
        return getattr(COUNTRIES[str(self)], name)


CountryParam = Annotated[
    _CountryParam,
    Path(
        description="Country code according to ISO 3166-1 alpha-2 codes, case insensitive.",
    ),
]

ValidRegion = Annotated[
    str,
    Path(
        description=(
            "Region identifier: `national`, a region `name` (case-insensitive), or a UUID. "
            "Use `GET /{country}/{source}/regions` to browse available regions."
        ),
        min_length=2,
        max_length=256,
    ),
]


def _check_window_start(v: dt.datetime | None) -> dt.datetime | None:
    if v is None:
        return None
    earliest = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=365)
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.UTC)
    if v < earliest:
        raise ValueError(
            "start_utc exceeds the 1-year rolling data limit. "
            f"Contact us at {SUPPORT_EMAIL} for access to extended history.",
        )
    return v


ValidWindowStart = Annotated[
    dt.datetime | None,
    Query(description="Start of window (UTC)."),
    AfterValidator(_check_window_start),
]


def _check_window_end(v: dt.datetime | None) -> dt.datetime | None:
    if v is None:
        return None
    latest = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=365)
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.UTC)
    if v > latest:
        raise ValueError(
            "end_utc is more than a year ahead. No forecast extends that far, so check "
            "the year, and that the value is an ISO 8601 timestamp rather than an epoch.",
        )
    return v


# The mirror of ValidWindowStart. It is not a data-availability limit the way the start
# is — it exists to catch a mistyped year or an epoch passed where a timestamp belongs,
# which otherwise returns an empty series and looks like missing data.
# The Query has to live in here rather than on each route. A route-level `= Query(...)`
# default does not error alongside an Annotated AfterValidator, it just silently skips
# the validator — so the per-route wording is given up to keep the check. Each route's
# docstring states its own default window.
ValidWindowEnd = Annotated[
    dt.datetime | None,
    Query(
        description=(
            "End of window (UTC). The default depends on the endpoint; see its "
            "description."
        ),
    ),
    AfterValidator(_check_window_end),
]


ValidObserver = Annotated[
    str | None,
    Query(
        description=(
            "The observer source name. If omitted, the country's first configured "
            "observer is used (see `/{country}/{source}/generation-sources`)."
        ),
        pattern=_build_observer_pattern(),
        examples=list(_get_observer_sources()),
        enum=list(_get_observer_sources()),
    ),
]


# Deprecated alias for `observer_name`, accepted so existing integrations keep working.
# Hidden from the OpenAPI schema for the same reason as `model`: a new client should
# only ever see the name we intend to keep. Dropped once no users are on it.
DeprecatedObserver = Annotated[
    str | None,
    Query(include_in_schema=False),
]


class Centroid(BaseModel):
    """Geographic centroid of a region."""

    lat: float
    lng: float

    @field_validator("lat", "lng")
    @classmethod
    def _round_3dp(cls, v: float) -> float:
        return round(v, 3)


class Source(BaseModel):
    """An available forecast source (energy type)."""

    name: str
    label: str


class ForecastModel(BaseModel):
    """A forecaster (model) available for a region type."""

    name: str
    label: str


class GenerationSource(BaseModel):
    """A generation (observation) source definition for a country."""

    source: str
    name: str
    label: str


class RegionType(BaseModel):
    """A region type definition for a country."""

    type: str
    label: str
    level: int
    default_model: str | None = None
    forecast_models: list[ForecastModel] = []
    supports_adjusted: bool = False
    """Whether the `adjusted` param has any effect for this region type.

    False means there are no trend-adjusted model variants at this granularity and the
    param is silently ignored.
    """


class CountryDetail(BaseModel):
    """Full capability manifest for a country: region types, models, and generation sources."""

    country: str
    name: str
    capacity_kW: float
    centroid: Centroid
    region_types: list[RegionType] = []
    generation_sources: list[GenerationSource] = []


class RegionSummary(BaseModel):
    """Summary of a region (nation, DNO, GSP, etc.)."""

    name: str
    type: str | None = None
    capacity_kW: float
    centroid: Centroid


class RegionDetail(RegionSummary):
    """Detailed region information including metadata."""

    metadata: dict[str, str | int | float] = Field(default_factory=dict)


class ForecastValue(BaseModel):
    """A single forecast value at a point in time.

    Everything below `plevels_kW` is populated only when `detail` is raised above
    `values`. See `DetailLevel`.
    """

    time_utc: dt.datetime
    power_kW: float
    plevels_kW: dict[str, float] = Field(default_factory=dict)

    last_updated_utc: dt.datetime | None = None
    latest_init_utc: dt.datetime | None = None
    model_name: str | None = None
    model_version: str | None = None
    capacity_kW: float | None = None
    metadata: dict | None = Field(
        default=None,
        description=(
            "Passthrough of the forecaster's own metadata, including when each input "
            "data source was last ingested. Keys vary by model and are not a stable "
            "contract, and will be replaced by a typed structure once all models run "
            "through one pipeline. Returned only when `detail=full`."
        ),
    )


class ForecastResponse(BaseModel):
    """Forecast time series for a region, with shared metadata.

    The data platform stitches the latest-run value for each target time, so a response
    normally spans several model runs. The fields below describe the response as a whole;
    use `detail=runs` for each value's own run.
    """

    region_name: str
    capacity_kW: float = Field(
        description=(
            "Effective capacity at the last target time in the response. Capacity varies "
            "over time, so on a long window earlier values may have had a different one; "
            "use `detail=runs` for per-value capacity."
            "N.B. this is now `effective` rather than the `installed` that was provided "
            "through the v0 API; this is still available if needed through metadata, but "
            "prefer this value as this is what we use internally to train and normalize by."
        ),
    )
    model_name: str | None = Field(
        default=None,
        description="Model that produced the response.",
    )
    model_version: str | None = Field(
        default=None,
        description="Version of that model.",
    )
    last_updated_utc: dt.datetime | None = Field(
        default=None,
        description=(
            "When the most recent run contributing to this response was created. Earlier "
            "values may come from earlier runs."
        ),
    )
    latest_init_utc: dt.datetime | None = Field(
        default=None,
        description="Init time of that most recent run.",
    )
    horizon_minutes: int | None = Field(
        default=None,
        description="Echo of the requested `horizon_minutes` filter, if any.",
    )
    metadata: dict | None = Field(
        default=None,
        description=(
            "Region-level extras, returned only when `detail=full`. Carries "
            "`installed_capacity_kW` where the platform has one: the capacity before "
            "degradation, which is what v0 reported as `installedCapacityMw` and what "
            "PV Live publishes. It is a few percent higher than `capacity_kW` (effective) "
            "and is **not** what the forecast is normalised against, so prefer `capacity_kW`. "
            "Retained for convenient reference and migration from v0."
        ),
    )
    values: list[ForecastValue]


class GenerationValue(BaseModel):
    """A single observed generation value at a point in time.

    `capacity_kW` is populated only when `detail` is raised above `values`. Observed
    values carry no model run, so there is nothing further for `detail=full` to add.
    """

    time_utc: dt.datetime
    power_kW: float

    capacity_kW: float | None = None


class GenerationResponse(BaseModel):
    """Observed generation time series for a region, with shared metadata."""

    region_name: str
    capacity_kW: float = Field(
        description=(
            "Effective capacity at the last target time in the response. Capacity varies "
            "over time; use `detail=runs` for per-value capacity."
        ),
    )
    observer_name: str | None = Field(
        default=None,
        description="Observer the values were recorded by, e.g. `pvlive_in_day`.",
    )
    metadata: dict | None = Field(
        default=None,
        description=(
            "Region-level extras, returned only when `detail=full`. Carries "
            "`installed_capacity_kW` where the platform has one: the capacity before "
            "degradation, which is what v0 reported as `installedCapacityMw` and what "
            "PV Live publishes. It is a few percent higher than `capacity_kW` and is "
            "**not** what the forecast is normalised against, so prefer `capacity_kW`. "
            "Retained for migration from v0."
        ),
    )
    values: list[GenerationValue]


class RegionForecastValue(BaseModel):
    """A single forecast value for one region, used in snapshot responses."""

    region_name: str
    capacity_kW: float
    power_kW: float
    plevels_kW: dict[str, float] | None = None


class ForecastSnapshot(BaseModel):
    """Snapshot forecast across all regions at a single point in time."""

    time_utc: dt.datetime = Field(
        description="The single target time every value in this snapshot is for.",
    )
    model_name: str | None = Field(
        default=None,
        description="Model that produced the snapshot.",
    )
    model_version: str | None = Field(default=None, description="Version of that model.")
    last_updated_utc: dt.datetime | None = Field(
        default=None,
        description=(
            "When the most recent run contributing to this snapshot was created. Regions "
            "are forecasted independently, so others may come from earlier runs."
        ),
    )
    latest_init_utc: dt.datetime | None = Field(
        default=None,
        description="Init time of that most recent run.",
    )
    values: list[RegionForecastValue]


class RegionGenerationValue(BaseModel):
    """A single observed generation value for one region, used in snapshot responses."""

    region_name: str
    capacity_kW: float
    power_kW: float


class GenerationSnapshot(BaseModel):
    """Snapshot observed generation across all regions at a single point in time."""

    time_utc: dt.datetime
    observer_name: str | None = None
    values: list[RegionGenerationValue]


class RegionForecast(BaseModel):
    """Forecast time series for one region, used in matrix responses."""

    region_name: str
    capacity_kW: float
    power_kW: list[float]
    plevels_kW: dict[str, list[float]] = Field(default_factory=dict)


class RegionForecastMatrix(BaseModel):
    """Forecast time series for all regions across a time window."""

    model_name: str | None = None
    model_version: str | None = None
    last_updated_utc: dt.datetime | None = None
    latest_init_utc: dt.datetime | None = None
    cache_updated_utc: dt.datetime | None = None
    times_utc: list[dt.datetime]
    regions: list[RegionForecast]


class RegionGeneration(BaseModel):
    """Generation time series for one region, used in matrix responses."""

    region_name: str
    capacity_kW: float
    power_kW: list[float]


class RegionGenerationMatrix(BaseModel):
    """Observed generation time series for all regions across a time window."""

    observer_name: str | None = None
    cache_updated_utc: dt.datetime | None = None
    times_utc: list[dt.datetime]
    regions: list[RegionGeneration]
