"""Country-specific configuration for the v1 API.

Maps URL country codes to data platform nation names and defines the available
region types, models, generation sources etc. per country.
"""

from dataclasses import dataclass

from quartz_api.internal.models import LocationType


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

    @property
    def api_name(self) -> str:
        """User-facing model name used in API params and enum values."""
        return self.slug if self.slug is not None else self.name

    def accepts(self, api_name: str) -> bool:
        """True if `api_name` names this model, current or legacy."""
        return (
            api_name == self.api_name
            or api_name in self.aliases
            or api_name in self.adjust_aliases
        )

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

    def get_display_name(self, internal_name: str) -> str | None:
        """Return the user-facing display name for a DP location name, or None if unmapped."""
        for dp_name, display in self.location_name_map:
            if dp_name == internal_name:
                return display
        return None

    def get_model_by_api_name(self, api_name: str) -> ForecastModel | None:
        """Look up a ForecastModel by its user-facing API name, current or legacy."""
        for fm in self.forecast_models:
            if fm.accepts(api_name):
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
    generation_sources: tuple[GenerationSource, ...] = ()
    permission: str = ""
    intraday_permission: str | None = None

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

    def resolve_observer(self, api_name: str) -> str:
        """Return the internal DP observer name for a user-facing API name."""
        for gs in self.generation_sources:
            if gs.api_name == api_name:
                return gs.name
        return api_name


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
        label="PV + ECMWF + Met Office + Satellite, Uncurtailed",
        slug="ecmwf_mo_sat_uncurtailed",
        adjust_name="nl_regional_pv_ecmwf_mo_sat_uncurtailed_adjust",
        adjust_aliases=("ecmwf_mo_sat_uncurtailed_adjust",),
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

COUNTRIES: dict[str, CountryConfig] = {
    "GB": CountryConfig(
        code="GB",  # used for path params / country-level differentiation
        nation_name="uk",  # maps to DP region name
        display_name="Great Britain",
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
    ),
    "NL": CountryConfig(
        code="NL",
        nation_name="nl_national",
        display_name="Nederland",
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
}

VALID_COUNTRY_CODES: set[str] = set(COUNTRIES.keys())
