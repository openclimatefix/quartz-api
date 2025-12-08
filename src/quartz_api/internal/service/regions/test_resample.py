import datetime as dt
import unittest

from quartz_api.internal.models import ActualPower

from ._resample import resample_generation


class TestResampleGeneration(unittest.TestCase):
    def test_resample_generation_1_period(self) -> None:

        values = [
            ActualPower(Time=dt.datetime.fromisoformat("2021-01-01T00:00:00Z"), PowerKW=1.0),
            ActualPower(Time=dt.datetime.fromisoformat("2021-01-01T00:08:00Z"), PowerKW=2.0),
        ]

        values = resample_generation(values, 15)

        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].Time, dt.datetime.fromisoformat("2021-01-01T00:00:00Z"))
        self.assertEqual(values[0].PowerKW, 1.5)


    def test_resample_generation_3_periods_with_gaps(self) -> None:

        values = [
            ActualPower(Time=dt.datetime.fromisoformat("2021-01-01T00:00:00Z"), PowerKW=1.0),
            ActualPower(Time=dt.datetime.fromisoformat("2021-01-01T00:08:00Z"), PowerKW=2.0),
            ActualPower(Time=dt.datetime.fromisoformat("2021-01-01T00:22:00Z"), PowerKW=3.0),
            ActualPower(Time=dt.datetime.fromisoformat("2021-01-01T01:30:00Z"), PowerKW=4.0),
            ActualPower(Time=dt.datetime.fromisoformat("2021-01-01T01:31:00Z"), PowerKW=5.0),
            ActualPower(Time=dt.datetime.fromisoformat("2021-01-01T01:32:00Z"), PowerKW=6.0),
        ]

        values = resample_generation(values, 15)

        self.assertEqual(len(values), 3)
        self.assertEqual(values[0].Time, dt.datetime.fromisoformat("2021-01-01T00:00:00Z"))
        self.assertEqual(values[0].PowerKW, 1.5)
        self.assertEqual(values[1].Time, dt.datetime.fromisoformat("2021-01-01T00:15:00Z"))
        self.assertEqual(values[1].PowerKW, 3.0)
        self.assertEqual(values[2].Time, dt.datetime.fromisoformat("2021-01-01T01:30:00Z"))
        self.assertEqual(values[2].PowerKW, 5.0)
