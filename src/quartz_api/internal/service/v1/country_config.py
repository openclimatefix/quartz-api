"""Country-specific configuration for the v1 API.

Maps URL country codes to data platform nation names and defines the available
region types per country.
"""

from dataclasses import dataclass

from quartz_api.internal.models import LocationType


@dataclass(frozen=True)
class RegionTypeConfig:
    """Configuration for a region type within a country."""

    type: str
    label: str
    level: int
    location_type: LocationType
    source_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class GenerationType:
    """Configuration for a generation source."""

    source: str
    name: str
    label: str


@dataclass(frozen=True)
class CountryConfig:
    """Configuration for a country."""

    nation_name: str
    region_types: tuple[RegionTypeConfig, ...]
    generation_types: tuple[GenerationType, ...] = ()

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

    def get_generation_type(self, source: str) -> GenerationType | None:
        """Look up a generation source by its user-facing name."""
        for gt in self.generation_types:
            if gt.source == source:
                return gt
        return None


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
            ),
            RegionTypeConfig(
                type="gsp",
                label="Grid Supply Point",
                level=10,
                location_type=LocationType.GSP,
                source_types=("solar",),
            ),
            RegionTypeConfig(
                type="dno",
                label="Distribution Network Operator",
                level=20,
                location_type=LocationType.DNO,
                source_types=("solar",),
            ),
        ),
        generation_types=(
            GenerationType(source="solar", name="pvlive_in_day", label="PV Live Estimated"),
            GenerationType(source="solar", name="pvlive_day_after", label="PV Live Updated"),
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
