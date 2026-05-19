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
    """

    name: str
    label: str
    slug: str | None = None

    @property
    def api_name(self) -> str:
        """User-facing model name used in API params and enum values."""
        return self.slug if self.slug is not None else self.name


@dataclass(frozen=True)
class RegionTypeConfig:
    """Configuration for a region type within a country."""

    type: str
    label: str
    level: int
    location_type: LocationType
    source_types: tuple[str, ...] = ()
    forecast_models: tuple[ForecastModel, ...] = ()
    # default_model stores the internal DP forecaster_name, not the user-facing slug.
    default_model: str | None = None
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
        """Look up a ForecastModel by its user-facing API name (slug or internal name)."""
        for fm in self.forecast_models:
            if fm.api_name == api_name:
                return fm
        return None

    def get_model_by_internal_name(self, name: str) -> ForecastModel | None:
        """Look up a ForecastModel by its internal DP name."""
        for fm in self.forecast_models:
            if fm.name == name:
                return fm
        return None

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

    # GB — blend
    BLEND = ForecastModel(name="blend", label="Blend")
    BLEND_ADJUST = ForecastModel(name="blend_adjust", label="Blend (Trend Adjusted)")
    # GB — PVNet single-source models
    PVNET_ECMWF = ForecastModel(name="pvnet_ecmwf", label="PVNet Intraday (ECMWF)")
    PVNET_ECMWF_ADJUST = ForecastModel(
        name="pvnet_ecmwf_adjust",
        label="PVNet Intraday (ECMWF, Trend Adjusted)",
    )
    PVNET_SAT = ForecastModel(
        name="pvnet_sat_only", label="PVNet Intraday (Satellite)", slug="pvnet_sat",
    )
    PVNET_SAT_ADJUST = ForecastModel(
        name="pvnet_sat_only_adjust",
        label="PVNet Intraday (Satellite, Trend Adjusted)",
        slug="pvnet_sat_adjust",
    )
    PVNET_UKV = ForecastModel(
        name="pvnet_ukv_only", label="PVNet Intraday (Met Office)", slug="pvnet_ukv",
    )
    PVNET_UKV_ADJUST = ForecastModel(
        name="pvnet_ukv_only_adjust",
        label="PVNet Intraday (Met Office, Trend Adjusted)",
        slug="pvnet_ukv_adjust",
    )
    # GB — PVNet v2 (intraday)
    PVNET_INTRADAY = ForecastModel(
        name="pvnet_v2", label="PVNet Intraday", slug="pvnet_intraday",
    )
    PVNET_INTRADAY_ADJUST = ForecastModel(
        name="pvnet_v2_adjust",
        label="PVNet Intraday (Trend Adjusted)",
        slug="pvnet_intraday_adjust",
    )
    # GB — PVNet day-ahead
    PVNET_DAY_AHEAD = ForecastModel(name="pvnet_day_ahead", label="PVNet Day Ahead")
    PVNET_DAY_AHEAD_ADJUST = ForecastModel(
        name="pvnet_day_ahead_adjust",
        label="PVNet Day Ahead (Trend Adjusted)",
    )
    # NL — blend (slugs match GB blend slugs so API is consistent across countries)
    NL_BLEND = ForecastModel(name="nl_blend", label="Blend", slug="blend")
    NL_BLEND_ADJUST = ForecastModel(
        name="nl_blend_adjust",
        label="Blend (Trend Adjusted)",
        slug="blend_adjust",
    )
    # NL — uncurtailed (regional and national)
    NL_UNCURTAILED = ForecastModel(
        name="nl_regional_ecmwf_mo_sat", label="Uncurtailed", slug="uncurtailed",
    )
    NL_UNCURTAILED_ADJUST = ForecastModel(
        name="nl_regional_ecmwf_mo_sat_adjust",
        label="Uncurtailed (Trend Adjusted)",
        slug="uncurtailed_adjust",
    )


_GB_NATIONAL_FORECAST_MODELS = (
    FM.BLEND,
    FM.BLEND_ADJUST,
    FM.PVNET_ECMWF,
    FM.PVNET_ECMWF_ADJUST,
    FM.PVNET_SAT,
    FM.PVNET_SAT_ADJUST,
    FM.PVNET_UKV,
    FM.PVNET_UKV_ADJUST,
    FM.PVNET_INTRADAY,
    FM.PVNET_INTRADAY_ADJUST,
    FM.PVNET_DAY_AHEAD,
    FM.PVNET_DAY_AHEAD_ADJUST,
)

_GB_GSP_FORECAST_MODELS = (
    FM.BLEND,
    FM.PVNET_INTRADAY,
    FM.PVNET_DAY_AHEAD,
)

_NL_NATIONAL_FORECAST_MODELS = (
    FM.NL_BLEND,
    FM.NL_BLEND_ADJUST,
    FM.NL_UNCURTAILED,
    FM.NL_UNCURTAILED_ADJUST,
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
                default_model="blend_adjust",
                intraday_models=(FM.PVNET_INTRADAY, FM.PVNET_INTRADAY_ADJUST),
                intraday_default_model=FM.PVNET_INTRADAY_ADJUST,
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
                intraday_models=(FM.PVNET_INTRADAY,),
                intraday_default_model=FM.PVNET_INTRADAY,
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
                default_model="nl_blend_adjust",
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
            GenerationSource(source="solar", name="nednl", label="NED NL Initial", slug="ned_nl"),
        ),
    ),
}

VALID_COUNTRY_CODES: set[str] = set(COUNTRIES.keys())
