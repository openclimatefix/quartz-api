"""Country-specific configuration for the v1 API.

Maps URL country codes to data platform nation names and defines the available
region types per country.
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
    # intraday_models lists internal DP names of models accessible to intraday-only users.
    # intraday_default_model is also an internal DP name.
    # Both are resolved to user-facing slugs at runtime via forecast_models.
    intraday_models: tuple[str, ...] = ()
    intraday_default_model: str | None = None

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

    def intraday_api_names(self) -> frozenset[str]:
        """Return the set of user-facing names for models in intraday_models."""
        internal = frozenset(self.intraday_models)
        return frozenset(
            fm.api_name for fm in self.forecast_models if fm.name in internal
        )


@dataclass(frozen=True)
class GenerationSource:
    """Configuration for a generation source."""

    source: str
    name: str
    label: str


@dataclass(frozen=True)
class CountryConfig:
    """Configuration for a country."""

    code: str
    nation_name: str
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


# Maps DP forecaster_name → human-readable label.
# Add an entry here whenever a new forecaster is registered in the data platform.
FORECASTER_LABELS: dict[str, str] = {
    # GB — blend
    "blend": "Blend",
    "blend_adjust": "Blend (Trend Adjusted)",
    # GB — PVNet intraday
    "pvnet_intra_sat30": "PVNet Intraday Satellite 30min",
    "pvnet_intra_sat30_adjust": "PVNet Intraday Satellite 30min (Trend Adjusted)",
    # GB — PVNet day-ahead
    "pvnet_day_ahead": "PVNet Day Ahead",
    "pvnet_day_ahead_adjust": "PVNet Day Ahead (Trend Adjusted)",
    "pvnet_da_2nwp": "PVNet Day Ahead (2 NWP)",
    "pvnet_da_2nwp_adjust": "PVNet Day Ahead (2 NWP, Trend Adjusted)",
    "pvnet_da_ecmwf": "PVNet Day Ahead (ECMWF)",
    "pvnet_da_ecmwf_adjust": "PVNet Day Ahead (ECMWF, Trend Adjusted)",
    "pvnet_da_ukv": "PVNet Day Ahead (UKV)",
    "pvnet_da_ukv_adjust": "PVNet Day Ahead (UKV, Trend Adjusted)",
    # GB — PVNet single-input intraday
    "pvnet_ecmwf": "PVNet Intraday (ECMWF only)",
    "pvnet_ecmwf_adjust": "PVNet Intraday (ECMWF only, Trend Adjusted)",
    "pvnet_sat_only": "PVNet Intraday (Satellite only)",
    "pvnet_sat_only_adjust": "PVNet Intraday (Satellite only, Trend Adjusted)",
    "pvnet_ukv_only": "PVNet Intraday (Met Office only)",
    "pvnet_ukv_only_adjust": "PVNet Intraday (Met Office only, Trend Adjusted)",
    # GB — PVNet v2 / cloud
    "pvnet_v2": "PVNet v2",
    "pvnet_v2_adjust": "PVNet v2 (Trend Adjusted)",
    "pvnet_cloud": "PVNet Cloud",
    "pvnet_cloud_adjust": "PVNet Cloud (Trend Adjusted)",
    # NL — regional
    "nl_regional_pv_ecmwf_mo_sat_adjust": "Regional PV (ECMWF + Met Office + Satellite, Trend Adjusted)",  # noqa: E501
    "nl_regional_pv_ecmwf_mo_sat": "Regional PV (ECMWF + Met Office + Satellite)",
    "nl_regional_2h_pv_ecmwf_adjust": "Regional PV 2h (ECMWF, Trend Adjusted)",
    "nl_regional_2h_pv_ecmwf": "Regional PV 2h (ECMWF)",
    "nl_regional_48h_pv_ecmwf_adjust": "Regional PV 48h (ECMWF, Trend Adjusted)",
    "nl_regional_48h_pv_ecmwf": "Regional PV 48h (ECMWF)",
    "nl_regional_pv_ecmwf_sat_adjust": "Regional PV (ECMWF + Satellite, Trend Adjusted)",
    "nl_regional_pv_ecmwf_sat": "Regional PV (ECMWF + Satellite)",
    # NL — national
    "nl_national_pv_ecmwf_sat_small_adjust": "National PV (ECMWF + Satellite, Trend Adjusted)",
    "nl_national_pv_ecmwf_sat_small": "National PV (ECMWF + Satellite)",
    "nl_36_simple_site_adjust": "36h Simple Site (Trend Adjusted)",
    "nl_36_simple_site": "36h Simple Site",
    "ned_nl_national": "NED National",
}


def _model(name: str, slug: str | None = None) -> ForecastModel:
    """Build a ForecastModel using the central label map (falls back to the raw name)."""
    return ForecastModel(name=name, label=FORECASTER_LABELS.get(name, name), slug=slug)


# Internal DP names for intraday models — used by intraday_models field on RegionTypeConfig.
_GB_INTRADAY_MODEL_NAMES: tuple[str, ...] = (
    "pvnet_ecmwf",
    "pvnet_ecmwf_adjust",
    "pvnet_sat_only",
    "pvnet_sat_only_adjust",
    "pvnet_ukv_only",
    "pvnet_ukv_only_adjust",
    "pvnet_v2",
    "pvnet_v2_adjust",
)

# Intraday ForecastModel entries shared across GB national, GSP, and DNO.
_GB_INTRADAY_FORECAST_MODELS: tuple[ForecastModel, ...] = (
    _model("pvnet_intra_sat30"),
    _model("pvnet_intra_sat30_adjust"),
    _model("pvnet_ecmwf"),
    _model("pvnet_ecmwf_adjust"),
    _model("pvnet_sat_only"),
    _model("pvnet_sat_only_adjust"),
    _model("pvnet_ukv_only"),
    _model("pvnet_ukv_only_adjust"),
    _model("pvnet_v2", slug="pvnet_intraday"),
    _model("pvnet_v2_adjust", slug="pvnet_intraday_adjust"),
)

_GB_NATIONAL_FORECAST_MODELS = (
    _model("blend"),
    _model("blend_adjust"),
    *_GB_INTRADAY_FORECAST_MODELS,
    _model("pvnet_day_ahead"),
    _model("pvnet_day_ahead_adjust"),
)

_GB_GSP_FORECAST_MODELS = (
    _model("blend"),
    _model("blend_adjust"),
    *_GB_INTRADAY_FORECAST_MODELS,
)

_NL_NATIONAL_FORECAST_MODELS = (
    _model("nl_blend"),
    _model("nl_blend_adjust"),
    _model("ned_nl_national"),
)

_NL_REGIONAL_FORECAST_MODELS = (
    _model("nl_blend"),
    _model("nl_blend_adjust"),
)

COUNTRIES: dict[str, CountryConfig] = {
    "GB": CountryConfig(
        code="GB",
        nation_name="uk",
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
                intraday_models=_GB_INTRADAY_MODEL_NAMES,
                intraday_default_model="pvnet_v2_adjust",
            ),
            RegionTypeConfig(
                type="gsp",
                label="Grid Supply Point",
                level=10,
                location_type=LocationType.GSP,
                source_types=("solar",),
                forecast_models=_GB_GSP_FORECAST_MODELS,
                default_model="blend_adjust",
                metadata_fields=("gsp_id", "full_name"),
                intraday_models=_GB_INTRADAY_MODEL_NAMES,
                intraday_default_model="pvnet_v2_adjust",
            ),
            RegionTypeConfig(
                type="dno",
                label="Distribution Network Operator",
                level=20,
                location_type=LocationType.DNO,
                source_types=("solar",),
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
        permission="read:nl",
        region_types=(
            RegionTypeConfig(
                type="national",
                label="National",
                level=0,
                location_type=LocationType.NATION,
                source_types=("solar",),
                forecast_models=_NL_NATIONAL_FORECAST_MODELS,
                default_model="blend_adjust",
            ),
            RegionTypeConfig(
                type="provinces",
                label="Provinces",
                level=10,
                location_type=LocationType.REGION,
                source_types=("solar",),
                forecast_models=_NL_REGIONAL_FORECAST_MODELS,
                default_model="blend_adjust",
                metadata_fields=("region_id",),
            ),
        ),
        generation_sources=(
            GenerationSource(source="solar", name="nednl", label="NED NL Initial"),
        ),
    ),
}

VALID_COUNTRY_CODES: set[str] = set(COUNTRIES.keys())
