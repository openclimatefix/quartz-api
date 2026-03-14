"""Country-specific configuration for the v1 API.

Maps URL country codes to data platform nation names and defines the available
region types per country.
"""

from dataclasses import dataclass

from quartz_api.internal.models import LocationType


@dataclass(frozen=True)
class ForecastModel:
    """A forecaster (model) available for a region type."""

    name: str
    label: str


@dataclass(frozen=True)
class RegionTypeConfig:
    """Configuration for a region type within a country."""

    type: str
    label: str
    level: int
    location_type: LocationType
    source_types: tuple[str, ...] = ()
    forecast_models: tuple[ForecastModel, ...] = ()


@dataclass(frozen=True)
class GenerationSource:
    """Configuration for a generation source."""

    source: str
    name: str
    label: str


@dataclass(frozen=True)
class CountryConfig:
    """Configuration for a country."""

    nation_name: str
    region_types: tuple[RegionTypeConfig, ...]
    generation_sources: tuple[GenerationSource, ...] = ()

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


_BLEND_ONLY = (ForecastModel(name="blend", label="Blend"),)

_NATIONAL_FORECAST_MODELS = (
    ForecastModel(name="blend", label="Blend"),
    ForecastModel(name="pvnet_intraday", label="PVNet Intraday"),
    ForecastModel(name="pvnet_day_ahead", label="PVNet Day Ahead"),
    ForecastModel(name="pvnet_intraday_ecmwf_only", label="PVNet Intraday (ECMWF only)"),
    ForecastModel(
        name="pvnet_intraday_met_office_only",
        label="PVNet Intraday (Met Office only)",
    ),
    ForecastModel(name="pvnet_intraday_sat_only", label="PVNet Intraday (Satellite only)"),
)

COUNTRIES: dict[str, CountryConfig] = {
    "GB": CountryConfig(
        nation_name="uk",
        region_types=(
            RegionTypeConfig(
                type="national",
                label="National",
                level=0,
                location_type=LocationType.NATION,
                source_types=("solar",),
                forecast_models=_NATIONAL_FORECAST_MODELS,
            ),
            RegionTypeConfig(
                type="gsp",
                label="Grid Supply Point",
                level=10,
                location_type=LocationType.GSP,
                source_types=("solar",),
                forecast_models=_BLEND_ONLY,
            ),
            RegionTypeConfig(
                type="dno",
                label="Distribution Network Operator",
                level=20,
                location_type=LocationType.DNO,
                source_types=("solar",),
                forecast_models=_BLEND_ONLY,
            ),
        ),
        generation_sources=(
            GenerationSource(source="solar", name="pvlive_in_day", label="PV Live Estimated"),
            GenerationSource(source="solar", name="pvlive_day_after", label="PV Live Updated"),
        ),
    ),
    "NL": CountryConfig(
        nation_name="nl",
        region_types=(
            RegionTypeConfig(
                type="national",
                label="National",
                level=0,
                location_type=LocationType.NATION,
                source_types=("solar",),
            ),
            RegionTypeConfig(
                type="netbeheerder",
                label="Network Operator",
                level=10,
                location_type=LocationType.DNO,
                source_types=(),
            ),
        ),
    ),
}

VALID_COUNTRY_CODES: set[str] = set(COUNTRIES.keys())
