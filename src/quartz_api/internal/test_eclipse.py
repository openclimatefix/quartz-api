"""Tests for the manual eclipse adjustment applied to national forecasts."""

import datetime as dt
import logging
from uuid import UUID, uuid4

import pytest

from . import eclipse, models

NATIONAL_UUID = uuid4()
GSP_UUID = uuid4()


@pytest.fixture(autouse=True)
def _eclipse_on(monkeypatch):
    monkeypatch.setattr(eclipse, "ECLIPSE_ENABLED", True)
    monkeypatch.setattr(eclipse, "ECLIPSE_DATE", dt.date(2026, 8, 12))


def _pgv(
    hour: int,
    minute: int,
    power_kw: float = 1000.0,
    location_uuid: UUID = NATIONAL_UUID,
) -> models.PredictedGenerationValue:
    return models.PredictedGenerationValue(
        power_kilowatts=power_kw,
        valid_timestamp=dt.datetime(2026, 8, 12, hour, minute, tzinfo=dt.UTC),
        location_uuid=location_uuid,
        capacity_kilowatts=2000.0,
        forecaster_name="blend_adjust",
        forecaster_version="1.3.0",
        plevels_kilowatts={"p10": 800.0, "p50": 1000.0, "p90": 1200.0},
    )


def test_factor_at_tabulated_time():
    assert eclipse.factor_for("GB", dt.datetime(2026, 8, 12, 18, 30, tzinfo=dt.UTC)) == 0.197790


def test_gb_and_nl_curves_are_independent():
    deepest_gb = dt.datetime(2026, 8, 12, 18, 30, tzinfo=dt.UTC)
    deepest_nl = dt.datetime(2026, 8, 12, 18, 15, tzinfo=dt.UTC)

    assert eclipse.factor_for("GB", deepest_gb) == 0.197790
    assert eclipse.factor_for("NL", deepest_nl) == 0.161150
    # 18:15 is not on the GB half-hourly grid.
    assert eclipse.factor_for("GB", deepest_nl) == 1.0
    assert eclipse.factor_for("NL", deepest_gb) == 0.299477


def test_factor_outside_window_and_off_date():
    assert eclipse.factor_for("GB", dt.datetime(2026, 8, 12, 12, 0, tzinfo=dt.UTC)) == 1.0
    assert eclipse.factor_for("GB", dt.datetime(2026, 8, 11, 18, 30, tzinfo=dt.UTC)) == 1.0


def test_factor_when_disabled_or_unknown_country(monkeypatch):
    ts = dt.datetime(2026, 8, 12, 18, 30, tzinfo=dt.UTC)
    assert eclipse.factor_for("IN", ts) == 1.0

    monkeypatch.setattr(eclipse, "ECLIPSE_ENABLED", False)
    assert eclipse.factor_for("GB", ts) == 1.0


def test_naive_and_offset_timestamps_are_normalised_to_utc():
    assert eclipse.factor_for("GB", dt.datetime(2026, 8, 12, 18, 30)) == 0.197790  # noqa: DTZ001

    # 19:30 BST is 18:30 UTC.
    bst = dt.datetime(2026, 8, 12, 19, 30, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    assert eclipse.factor_for("GB", bst) == 0.197790


def test_in_window_grid_miss_warns(caplog):
    with caplog.at_level(logging.WARNING):
        assert eclipse.factor_for("GB", dt.datetime(2026, 8, 12, 18, 20, tzinfo=dt.UTC)) == 1.0
    assert "not on the 30-minute table grid" in caplog.text


def test_snapshot_factor_rounds_onto_the_grid():
    assert eclipse.snapshot_factor_for(
        "GB", dt.datetime(2026, 8, 12, 18, 20, tzinfo=dt.UTC),
    ) == 0.197790
    assert eclipse.snapshot_factor_for(
        "GB", dt.datetime(2026, 8, 12, 18, 10, tzinfo=dt.UTC),
    ) == 0.566288
    # NL rounds on a 15-minute grid, so 18:20 lands on 18:15.
    assert eclipse.snapshot_factor_for(
        "NL", dt.datetime(2026, 8, 12, 18, 20, tzinfo=dt.UTC),
    ) == 0.161150
    on_grid = dt.datetime(2026, 8, 12, 18, 30, tzinfo=dt.UTC)
    assert eclipse.snapshot_factor_for("GB", on_grid) == eclipse.factor_for("GB", on_grid)


def test_adjust_scales_power_and_every_plevel():
    [adjusted] = eclipse.adjust_predicted_generation([_pgv(18, 30)], "GB")

    assert adjusted.power_kilowatts == pytest.approx(1000.0 * 0.197790)
    assert adjusted.plevels_kilowatts["p10"] == pytest.approx(800.0 * 0.197790)
    assert adjusted.plevels_kilowatts["p50"] == pytest.approx(1000.0 * 0.197790)
    assert adjusted.plevels_kilowatts["p90"] == pytest.approx(1200.0 * 0.197790)
    assert adjusted.capacity_kilowatts == 2000.0
    assert adjusted.forecaster_name == "blend_adjust"


def test_adjust_leaves_values_outside_the_window_alone():
    [adjusted] = eclipse.adjust_predicted_generation([_pgv(12, 0)], "GB")

    assert adjusted.power_kilowatts == 1000.0
    assert adjusted.plevels_kilowatts == {"p10": 800.0, "p50": 1000.0, "p90": 1200.0}


def test_adjust_does_not_mutate_the_input_values():
    original = _pgv(18, 30)
    eclipse.adjust_predicted_generation([original], "GB")

    assert original.power_kilowatts == 1000.0
    assert original.plevels_kilowatts["p90"] == 1200.0


def test_adjust_national_only_skips_other_locations():
    values = [
        _pgv(18, 30, location_uuid=NATIONAL_UUID),
        _pgv(18, 30, location_uuid=GSP_UUID),
    ]
    national, gsp = eclipse.adjust_national_only(values, "GB", NATIONAL_UUID)

    assert national.power_kilowatts == pytest.approx(1000.0 * 0.197790)
    assert gsp.power_kilowatts == 1000.0
