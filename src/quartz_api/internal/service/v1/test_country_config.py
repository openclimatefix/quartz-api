"""Tests for the deployment filter over the v1 country catalogue."""

from dataclasses import replace

import pytest
from fastapi import HTTPException

from quartz_api.internal.models import LocationType

from .country_config import (
    ALL_COUNTRIES,
    CountryConfig,
    ForecastModel,
    RegionTypeConfig,
    filter_for_deployment,
)
from .helpers import validate_model

_PROD_MODEL = ForecastModel(name="prod_model", label="Prod")
_DEV_MODEL = ForecastModel(
    name="dev_model",
    label="Dev",
    slug="dev",
    aliases=("old_dev",),
    stage="development",
)

_NATIONAL = RegionTypeConfig(
    type="national",
    label="National",
    level=0,
    location_type=LocationType.NATION,
    forecast_models=(_PROD_MODEL, _DEV_MODEL),
    default_model="prod_model",
)
_DEV_REGIONAL = RegionTypeConfig(
    type="regional",
    label="Regional",
    level=10,
    location_type=LocationType.REGION,
    forecast_models=(_DEV_MODEL,),
    default_model="dev_model",
    stage="development",
)


def _country(code: str, stage: str = "production") -> CountryConfig:
    return CountryConfig(
        code=code,
        nation_name=code.lower(),
        display_name=code,
        time_step_minutes=30,
        region_types=(_NATIONAL, _DEV_REGIONAL),
        stage=stage,
    )


_CATALOGUE = {"AA": _country("AA"), "BB": _country("BB"), "DD": _country("DD", "development")}


def test_prod_hides_dev_countries_region_types_and_models() -> None:
    served = filter_for_deployment(_CATALOGUE, None, "production")
    assert sorted(served) == ["AA", "BB"]
    rts = served["AA"].region_types
    assert [rt.type for rt in rts] == ["national"]
    assert rts[0].forecast_models == (_PROD_MODEL,)


def test_dev_shows_everything() -> None:
    served = filter_for_deployment(_CATALOGUE, None, "development")
    assert served == _CATALOGUE


@pytest.mark.parametrize("countries", [None, "", " "])
def test_no_allowlist_serves_every_country(countries: str | None) -> None:
    assert sorted(filter_for_deployment(_CATALOGUE, countries, "development")) == ["AA", "BB", "DD"]


def test_allowlist_is_case_and_space_insensitive() -> None:
    assert sorted(filter_for_deployment(_CATALOGUE, " bb, dd ", "development")) == ["BB", "DD"]


def test_allowlisted_dev_country_is_still_hidden_on_prod() -> None:
    assert list(filter_for_deployment(_CATALOGUE, "AA,DD", "production")) == ["AA"]


def test_unknown_country_code_fails() -> None:
    with pytest.raises(ValueError, match="unknown codes \\['ZZ'\\]"):
        filter_for_deployment(_CATALOGUE, "AA,ZZ", "production")


def test_unknown_stage_fails() -> None:
    """The short forms are not accepted; the stage values match `api.environment`."""
    with pytest.raises(ValueError, match="V1_STAGE"):
        filter_for_deployment(_CATALOGUE, None, "prod")


def test_hidden_default_model_fails() -> None:
    rt = replace(_NATIONAL, default_model="dev_model")
    catalogue = {"AA": replace(_country("AA"), region_types=(rt,))}
    with pytest.raises(ValueError, match="default model 'dev_model'"):
        filter_for_deployment(catalogue, None, "production")


def test_hidden_intraday_default_fails() -> None:
    rt = replace(_NATIONAL, intraday_models=(_DEV_MODEL,), intraday_default_model=_DEV_MODEL)
    catalogue = {"AA": replace(_country("AA"), region_types=(rt,))}
    with pytest.raises(ValueError, match="intraday default model 'dev_model'"):
        filter_for_deployment(catalogue, None, "production")


def test_dev_only_model_is_unrequestable_on_prod() -> None:
    """A development model in a production region type is hidden and rejected."""
    rt = filter_for_deployment(_CATALOGUE, "AA", "production")["AA"].region_types[0]

    assert rt.get_model_by_api_name("dev") is None
    assert rt.get_model_by_api_name("old_dev") is None
    assert rt.get_model_by_internal_name("dev_model") is None

    with pytest.raises(HTTPException) as exc:
        validate_model("dev", rt, rt.label)
    assert exc.value.status_code == 400
    # The 400 lists what is available, so it must not leak the hidden name either.
    assert "dev" not in exc.value.detail.split("Available: ")[1]


def test_dev_only_model_is_requestable_on_dev() -> None:
    rt = filter_for_deployment(_CATALOGUE, "AA", "development")["AA"].region_types[0]

    assert rt.get_model_by_api_name("dev") == _DEV_MODEL
    assert rt.get_model_by_api_name("old_dev") == _DEV_MODEL
    validate_model("dev", rt, rt.label)


@pytest.mark.parametrize("stage", ["development", "production"])
def test_real_catalogue_is_valid_at_every_stage(stage: str) -> None:
    """A dev-tagged default in a prod region type would only fail on deploy."""
    filter_for_deployment(ALL_COUNTRIES, None, stage)
