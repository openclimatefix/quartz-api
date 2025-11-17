from quartz_api.internal.models import ActualPower
from quartz_api.internal.service.resample import resample_generation
import pandas as pd


def test_resample_generation_1_period():

    values = [
        ActualPower(Time="2021-01-01T00:00:00Z", PowerKW=1.0),
        ActualPower(Time="2021-01-01T00:08:00Z", PowerKW=2.0),
    ]

    values = resample_generation(values, 15)

    assert len(values) == 1
    assert values[0].Time == pd.Timestamp("2021-01-01T00:00:00Z")
    assert values[0].PowerKW == 1.5


def test_resample_generation_3_periods_with_gaps():

    values = [
        ActualPower(Time="2021-01-01T00:00:00Z", PowerKW=1.0),
        ActualPower(Time="2021-01-01T00:08:00Z", PowerKW=2.0),
        ActualPower(Time="2021-01-01T00:22:00Z", PowerKW=3.0),
        ActualPower(Time="2021-01-01T01:30:00Z", PowerKW=4.0),
        ActualPower(Time="2021-01-01T01:31:00Z", PowerKW=5.0),
        ActualPower(Time="2021-01-01T01:32:00Z", PowerKW=6.0),
    ]

    values = resample_generation(values, 15)

    assert len(values) == 3
    assert values[0].Time == pd.Timestamp("2021-01-01T00:00:00Z")
    assert values[0].PowerKW == 1.5
    assert values[1].Time == pd.Timestamp("2021-01-01T00:15:00Z")
    assert values[1].PowerKW == 3.0
    assert values[2].Time == pd.Timestamp("2021-01-01T01:30:00Z")
    assert values[2].PowerKW == 5.0
