"""Country-specific configuration for the v1 API.

Maps URL country codes to data platform nation names and defines the available
region types, models, generation sources etc. per country.
"""

import datetime as dt
import os
from dataclasses import dataclass, replace
from typing import Literal

import pandas as pd

from quartz_api.internal.models import LocationType

# `development` entries are served only where V1_STAGE=development. Promoting one to
# production is done through a PR. The values match `api.environment` so infra can pass
# the environment name straight through.
Stage = Literal["development", "production"]
STAGES: tuple[Stage, ...] = ("development", "production")


@dataclass(frozen=True)
class ForecastModel:
    """A forecaster (model) available for a region type.

    `name` is the internal DP forecaster_name sent to the data platform.
    `slug` is the user-facing API name (defaults to `name` when not set).
    `label` is the human-readable display name.
    `adjust_name` is the trend-adjusted variant the `adjusted` param selects; None
    means there is none and the param is a no-op here.

    `aliases` and `adjust_aliases` are retired user-facing names, still accepted but
    absent from `/region-types`, the OpenAPI enum and error messages. `aliases` pins
    the adjuster off and `adjust_aliases` pins it on, so an old name keeps the exact
    forecast it returned before the adjuster became a parameter.
    """

    name: str
    label: str
    slug: str | None = None
    adjust_name: str | None = None
    aliases: tuple[str, ...] = ()
    adjust_aliases: tuple[str, ...] = ()
    stage: Stage = "production"

    @property
    def api_name(self) -> str:
        """User-facing model name used in API params and enum values."""
        return self.slug if self.slug is not None else self.name

    def alias_adjust_override(self, api_name: str) -> bool | None:
        """Trend-adjuster state implied by a legacy name, or None if it implies nothing."""
        if api_name in self.adjust_aliases:
            return True
        if api_name in self.aliases:
            return False
        return None

    def internal_name(self, *, adjusted: bool) -> str:
        """Internal DP forecaster_name for this model at the given adjuster setting."""
        if adjusted and self.adjust_name is not None:
            return self.adjust_name
        return self.name


@dataclass(frozen=True)
class RegionTypeConfig:
    """Configuration for a region type within a country."""

    type: str
    label: str
    level: int
    location_type: LocationType
    source_types: tuple[str, ...] = ()
    forecast_models: tuple[ForecastModel, ...] = ()
    # Internal DP forecaster_name, unadjusted — the `adjusted` param reaches the variant.
    default_model: str | None = None
    # False (e.g. GB gsp) means no adjusted variants exist, so `adjusted` is a no-op.
    supports_adjusted: bool = False
    metadata_fields: tuple[str, ...] = ()
    # intraday_models lists models accessible to intraday-only users (subset of forecast_models).
    intraday_models: tuple[ForecastModel, ...] = ()
    intraday_default_model: ForecastModel | None = None
    # Maps internal DP location names to user-facing display names.
    # Entries not listed fall back to loc.name unchanged.
    location_name_map: tuple[tuple[str, str], ...] = ()
    stage: Stage = "production"

    def get_display_name(self, internal_name: str) -> str | None:
        """Return the user-facing display name for a DP location name, or None if unmapped."""
        for dp_name, display in self.location_name_map:
            if dp_name == internal_name:
                return display
        return None

    def get_model_by_api_name(self, api_name: str) -> ForecastModel | None:
        """Look up a ForecastModel by its user-facing API name, current or legacy.

        An `_adjust` alias names a variant that does not exist where this region type
        has no adjusted models, so it is unknown there rather than resolving to the
        plain forecast.
        """
        for fm in self.forecast_models:
            if api_name in (fm.api_name, *fm.aliases):
                return fm
            if self.supports_adjusted and api_name in fm.adjust_aliases:
                return fm
        return None

    def get_model_by_internal_name(self, name: str) -> ForecastModel | None:
        """Look up a ForecastModel by its internal DP name, adjusted or not."""
        for fm in self.forecast_models:
            if name in (fm.name, fm.adjust_name):
                return fm
        return None

    def default_forecaster_name(self, *, adjusted: bool = True) -> str | None:
        """Internal DP forecaster_name for the default model at the given adjuster setting."""
        if self.default_model is None:
            return None
        fm = self.get_model_by_internal_name(self.default_model)
        if fm is None:
            return self.default_model
        return fm.internal_name(
            adjusted=adjusted and self.supports_adjusted,
        )

    def default_model_api_name(self) -> str | None:
        """User-facing API name for the default forecast model."""
        if self.default_model is None:
            return None
        fm = self.get_model_by_internal_name(self.default_model)
        return fm.api_name if fm else self.default_model

    def intraday_api_names(self) -> frozenset[str]:
        """Return the set of user-facing names for intraday-accessible models."""
        return frozenset(fm.api_name for fm in self.intraday_models)


@dataclass(frozen=True)
class SiteConfig:
    """Per-country, per-energy-source configuration for site-level data."""

    source: str
    observer_name: str
    default_forecaster_name: str | None = None


@dataclass(frozen=True)
class GenerationSource:
    """Configuration for a generation source.

    `name` is the internal DP observer name. `slug` is the user-facing API name;
    defaults to `name` when not set.
    """

    source: str
    name: str
    label: str
    slug: str | None = None

    @property
    def api_name(self) -> str:
        """User-facing observer name used in API params."""
        return self.slug if self.slug is not None else self.name


@dataclass(frozen=True)
class CountryConfig:
    """Configuration for a country."""

    code: str
    nation_name: str  # internal DP location name — used only for DB lookups
    display_name: str  # user-facing nation name returned in API responses
    region_types: tuple[RegionTypeConfig, ...]
    # Minutes between consecutive values, forecast and observed alike. Snapshot times
    # are floored to it, so it must match the data: too coarse and valid times can
    # never be requested, too fine and a request matches nothing. No default, so a
    # new country has to state it.
    time_step_minutes: int
    generation_sources: tuple[GenerationSource, ...] = ()
    permission: str = ""
    intraday_permission: str | None = None
    site_configs: tuple[SiteConfig, ...] = ()
    stage: Stage = "production"

    def get_region_type(self, type_name: str) -> RegionTypeConfig | None:
        """Look up a region type by its user-facing name."""
        for rt in self.region_types:
            if rt.type == type_name:
                return rt
        return None

    def location_type_to_region_type(
        self,
        location_type: LocationType,
    ) -> RegionTypeConfig | None:
        """Look up a region type by its internal LocationType."""
        for rt in self.region_types:
            if rt.location_type == location_type:
                return rt
        return None

    def get_generation_source(self, source: str) -> GenerationSource | None:
        """Look up a generation source by its user-facing name."""
        for gt in self.generation_sources:
            if gt.source == source:
                return gt
        return None

    def get_site_config(self, source: str) -> SiteConfig | None:
        """Look up site-level config for a given energy source ('solar'/'wind')."""
        for sc in self.site_configs:
            if sc.source == source:
                return sc
        return None

    def default_observer(self, source: str) -> str | None:
        """Return the observer used when a request does not name one.

        The first configured source wins, so the order in `generation_sources` is the
        default order. Countries observe different things — GB has PV Live, NL has NED —
        so there is no one literal that works as a cross-country default.
        """
        for gs in self.generation_sources:
            if gs.source == source:
                return gs.api_name
        return None

    def resolve_observer(self, api_name: str) -> str:
        """Return the internal DP observer name for a user-facing API name."""
        for gs in self.generation_sources:
            if gs.api_name == api_name:
                return gs.name
        return api_name

    def floor_to_time_step(self, ts: dt.datetime) -> dt.datetime:
        """Floor a timestamp to this country's time step, as UTC.

        A naive timestamp is taken to be UTC already. Any other offset is converted
        first, so the floor falls on the UTC grid: a +05:45 time floored locally lands
        on :15 or :45 UTC.
        """
        stamp = pd.Timestamp(ts)
        stamp = stamp.tz_localize(dt.UTC) if stamp.tzinfo is None else stamp.tz_convert(dt.UTC)
        return stamp.floor(f"{self.time_step_minutes}min").to_pydatetime()


class FM:
    """All API-ready forecast models defined once — name, label, and slug in one place.

    This is the single source of truth. Add a new model here before referencing it
    in any RegionTypeConfig tuple.
    """

    # GB — blend of all models
    BLEND = ForecastModel(
        name="blend",
        label="Blend",
        adjust_name="blend_adjust",
        adjust_aliases=("blend_adjust",),
    )
    # GB — NWP-only day-ahead (ECMWF + Met Office, no satellite at day-ahead range)
    ECMWF_MO = ForecastModel(
        name="pvnet_day_ahead",
        label="ECMWF + Met Office",
        slug="ecmwf_mo",
        adjust_name="pvnet_day_ahead_adjust",
        aliases=("pvnet_day_ahead",),
        adjust_aliases=("pvnet_day_ahead_adjust",),
    )
    # GB — full intraday input set
    ECMWF_MO_SAT_8H = ForecastModel(
        name="pvnet_v2",
        label="ECMWF + Met Office + Satellite (8h)",
        slug="ecmwf_mo_sat_8h",
        adjust_name="pvnet_v2_adjust",
        aliases=("pvnet_intraday",),
        adjust_aliases=("pvnet_intraday_adjust",),
    )
    # GB — single-input ablation models
    ECMWF = ForecastModel(
        name="pvnet_ecmwf",
        label="ECMWF",
        slug="ecmwf",
        adjust_name="pvnet_ecmwf_adjust",
        aliases=("pvnet_ecmwf",),
        adjust_aliases=("pvnet_ecmwf_adjust",),
    )
    SAT_8H = ForecastModel(
        name="pvnet_sat_only",
        label="Satellite (8h)",
        slug="sat_8h",
        adjust_name="pvnet_sat_only_adjust",
        aliases=("pvnet_sat",),
        adjust_aliases=("pvnet_sat_adjust",),
    )
    MO = ForecastModel(
        name="pvnet_ukv_only",
        label="Met Office",
        slug="mo",
        adjust_name="pvnet_ukv_only_adjust",
        aliases=("pvnet_ukv",),
        adjust_aliases=("pvnet_ukv_adjust",),
    )
    # NL — blend (slugs match GB blend slugs so API is consistent across countries)
    NL_BLEND = ForecastModel(
        name="nl_blend",
        label="Blend",
        slug="blend",
        adjust_name="nl_blend_adjust",
        adjust_aliases=("blend_adjust",),
    )
    # NL — uncurtailed (regional and national)
    NL_UNCURTAILED = ForecastModel(
        name="nl_regional_pv_ecmwf_mo_sat_uncurtailed",
        label="ECMWF + Met Office + PV + Satellite, Uncurtailed",
        slug="ecmwf_mo_pv_sat_uncurtailed",
        adjust_name="nl_regional_pv_ecmwf_mo_sat_uncurtailed_adjust",
        aliases=("ecmwf_mo_sat_uncurtailed",),
        adjust_aliases=("ecmwf_mo_sat_uncurtailed_adjust",),
    )
    # DE — slugs follow the GB/NL naming: blend is the default, the rest are
    # the single/partial input-set models behind it.
    DE_BLEND = ForecastModel(
        name="de_blend",
        label="Blend",
        slug="blend",
        adjust_name="de_blend_adjust",
    )
    DE_ECMWF_PV = ForecastModel(
        name="de_ecmwf_pv",
        label="ECMWF + PV",
        slug="ecmwf_pv",
        adjust_name="de_ecmwf_pv_adjust",
    )
    DE_ECMWF = ForecastModel(
        name="de_ecmwf_only",
        label="ECMWF",
        slug="ecmwf",
        adjust_name="de_ecmwf_only_adjust",
    )
    DE_MO = ForecastModel(
        name="de_mo_only",
        label="Met Office",
        slug="mo",
        adjust_name="de_mo_only_adjust",
    )
    DE_SAT = ForecastModel(
        name="de_sat_only",
        label="Satellite (8h)",
        slug="sat_8h",
        adjust_name="de_sat_only_adjust",
    )
    DE_PV = ForecastModel(
        name="de_pv_only",
        label="PV",
        slug="pv",
        adjust_name="de_pv_only_adjust",
    )


_GB_NATIONAL_FORECAST_MODELS = (
    FM.BLEND,
    FM.ECMWF_MO,
    FM.ECMWF_MO_SAT_8H,
    FM.ECMWF,
    FM.SAT_8H,
    FM.MO,
)

_GB_GSP_FORECAST_MODELS = (
    FM.BLEND,
    FM.ECMWF_MO_SAT_8H,
    FM.ECMWF_MO,
)

_NL_NATIONAL_FORECAST_MODELS = (
    FM.NL_BLEND,
    FM.NL_UNCURTAILED,
)

_NL_REGIONAL_FORECAST_MODELS = (FM.NL_BLEND, FM.NL_UNCURTAILED)

_DE_FORECAST_MODELS = (
    FM.DE_BLEND,
    FM.DE_ECMWF_PV,
    FM.DE_ECMWF,
    FM.DE_MO,
    FM.DE_SAT,
    FM.DE_PV,
)

# Every country the API knows about. Only `COUNTRIES` below is served.
ALL_COUNTRIES: dict[str, CountryConfig] = {
    "GB": CountryConfig(
        code="GB",  # used for path params / country-level differentiation
        nation_name="uk",  # maps to DP region name
        display_name="Great Britain",
        time_step_minutes=30,
        permission="read:gb",
        intraday_permission="read:uk-intraday",
        region_types=(
            RegionTypeConfig(
                type="national",
                label="National",
                level=0,
                location_type=LocationType.NATION,
                source_types=("solar",),
                forecast_models=_GB_NATIONAL_FORECAST_MODELS,
                default_model="blend",
                supports_adjusted=True,
                intraday_models=(FM.ECMWF_MO_SAT_8H,),
                intraday_default_model=FM.ECMWF_MO_SAT_8H,
            ),
            RegionTypeConfig(
                type="gsp",
                label="Grid Supply Point",
                level=10,
                location_type=LocationType.GSP,
                source_types=("solar",),
                forecast_models=_GB_GSP_FORECAST_MODELS,
                default_model="blend",
                metadata_fields=("gsp_id", "full_name"),
                intraday_models=(FM.ECMWF_MO_SAT_8H,),
                intraday_default_model=FM.ECMWF_MO_SAT_8H,
            ),
        ),
        generation_sources=(
            GenerationSource(
                source="solar",
                name="pvlive_in_day",
                label="PV Live Estimated",
            ),
            GenerationSource(
                source="solar",
                name="pvlive_day_after",
                label="PV Live Updated",
            ),
        ),
        site_configs=(
            SiteConfig(
                source="solar",
                observer_name="pv_actual",
                default_forecaster_name=None,
            ),
        ),
    ),
    "NL": CountryConfig(
        code="NL",
        nation_name="nl_national",
        display_name="Nederland",
        time_step_minutes=15,
        permission="read:nl",
        region_types=(
            RegionTypeConfig(
                type="national",
                label="National",
                level=0,
                location_type=LocationType.NATION,
                source_types=("solar",),
                forecast_models=_NL_NATIONAL_FORECAST_MODELS,
                default_model="nl_blend",
                supports_adjusted=True,
            ),
            RegionTypeConfig(
                type="province",
                label="Province",
                level=10,
                location_type=LocationType.REGION,
                source_types=("solar",),
                forecast_models=_NL_REGIONAL_FORECAST_MODELS,
                default_model="nl_blend",
                metadata_fields=("region_id",),
                # Maps DP location names → user-facing display names.
                # N.B. EXISTING NAMES SHOULD NOT BE CHANGED AFTER DEPLOYMENT
                # to prevent breaking of any hard-codings in client scripts.
                # Adding additional names (although very unlikely) would be fine.
                location_name_map=(
                    ("nl_region_1_groningen", "groningen"),
                    ("nl_region_2_friesland", "friesland"),
                    ("nl_region_3_drenthe", "drenthe"),
                    ("nl_region_4_overijssel", "overijssel"),
                    ("nl_region_5_flevoland", "flevoland"),
                    ("nl_region_6_gelderland", "gelderland"),
                    ("nl_region_7_utrecht", "utrecht"),
                    ("nl_region_8_noord_holland", "noord-holland"),
                    ("nl_region_9_zuid_holland", "zuid-holland"),
                    ("nl_region_10_zeeland", "zeeland"),
                    ("nl_region_11_noord_brabant", "noord-brabant"),
                    ("nl_region_12_limburg", "limburg"),
                ),
            ),
        ),
        generation_sources=(
            GenerationSource(
                source="solar",
                name="nednl",
                label="NED NL Initial",
                slug="ned_nl",
            ),
        ),
    ),
    "DE": CountryConfig(
        code="DE",
        nation_name="de_national",
        display_name="Deutschland",
        time_step_minutes=15,
        permission="read:de",
        stage="development",
        region_types=(
            RegionTypeConfig(
                type="national",
                label="National",
                level=0,
                location_type=LocationType.NATION,
                source_types=("solar",),
                forecast_models=_DE_FORECAST_MODELS,
                default_model="de_blend",
                supports_adjusted=True,
            ),
            RegionTypeConfig(
                type="tso",
                label="Transmission System Operator",
                level=10,
                # Assumed to be stored as STATE in the DP, like the NL provinces.
                location_type=LocationType.REGION,
                source_types=("solar",),
                forecast_models=_DE_FORECAST_MODELS,
                default_model="de_blend",
                # No `_adjust` forecasts exist at TSO level, only national.
                supports_adjusted=False,
                # Same rule as NL: once deployed, existing names must not change.
                location_name_map=(
                    ("de_50hertz", "50hertz"),
                    ("de_amprion", "amprion"),
                    ("de_tennet", "tennet"),
                    ("de_transnetbw", "transnetbw"),
                ),
            ),
        ),
        generation_sources=(
            GenerationSource(
                source="solar",
                name="entsoe_de",
                label="ENTSO-E DE",
            ),
        ),
    ),
}



def parse_stage(value: str) -> Stage:
    """Validate a raw stage string from the environment into a `Stage`.

    Returns the matching `STAGES` member rather than `value` itself, so the
    narrowing from `str` is something the type checker can follow.
    """
    for stage in STAGES:
        if value == stage:
            return stage
    raise ValueError(f"V1_STAGE must be one of {list(STAGES)}, got '{value}'")


def _visible(entry: ForecastModel | RegionTypeConfig | CountryConfig, stage: Stage) -> bool:
    return stage == "development" or entry.stage == "production"


def _filter_region_type(rt: RegionTypeConfig, stage: Stage) -> RegionTypeConfig:
    models = tuple(fm for fm in rt.forecast_models if _visible(fm, stage))
    intraday = tuple(fm for fm in rt.intraday_models if _visible(fm, stage))
    names = {fm.name for fm in models}
    # A hidden default would make every request without `model_name` fail at the DP.
    if rt.default_model is not None and rt.default_model not in names:
        raise ValueError(
            f"Region type '{rt.type}': default model '{rt.default_model}' "
            f"is not visible at stage '{stage}'",
        )
    if rt.intraday_default_model is not None and rt.intraday_default_model not in intraday:
        raise ValueError(
            f"Region type '{rt.type}': intraday default model "
            f"'{rt.intraday_default_model.name}' is not visible at stage '{stage}'",
        )
    return replace(rt, forecast_models=models, intraday_models=intraday)


def filter_for_deployment(
    catalogue: dict[str, CountryConfig],
    countries: str | None,
    stage: str,
) -> dict[str, CountryConfig]:
    """Return the countries, region types and models this deployment serves.

    `countries` is a comma-separated allowlist of country codes; None or empty
    serves every country in the catalogue. `stage` hides `dev` entries unless it
    is `dev`. Raises ValueError on a bad value, so a misconfigured deployment
    fails at startup.
    """
    checked_stage = parse_stage(stage)
    codes = [c.strip().upper() for c in (countries or "").split(",") if c.strip()]
    unknown = sorted(set(codes) - catalogue.keys())
    if unknown:
        raise ValueError(
            f"V1_COUNTRIES has unknown codes {unknown}. Known: {sorted(catalogue)}",
        )
    selected = [cfg for code, cfg in catalogue.items() if not codes or code in codes]
    return {
        cfg.code: replace(
            cfg,
            region_types=tuple(
                _filter_region_type(rt, checked_stage)
                for rt in cfg.region_types
                if _visible(rt, checked_stage)
            ),
        )
        for cfg in selected
        if _visible(cfg, checked_stage)
    }


# Read from the environment at import time: the OpenAPI enums in endpoint_types
# are built from COUNTRIES when that module is imported, so this must come first.
# V1_STAGE defaults to production so a deployment that forgets it never shows dev entries.
DEPLOYMENT_COUNTRIES: str | None = os.environ.get("V1_COUNTRIES")
DEPLOYMENT_STAGE: str = os.environ.get("V1_STAGE", "production")

COUNTRIES: dict[str, CountryConfig] = filter_for_deployment(
    ALL_COUNTRIES,
    DEPLOYMENT_COUNTRIES,
    DEPLOYMENT_STAGE,
)

VALID_COUNTRY_CODES: set[str] = set(COUNTRIES.keys())
