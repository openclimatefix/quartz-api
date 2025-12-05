"""A dummy database that conforms to the DatabaseInterface."""
# ruff: noqa: S311

import datetime as dt
import math
import random
from uuid import UUID, uuid4

from typing_extensions import override

from quartz_api.internal import models

from ..utils import get_window
from ._models import DummyDBPredictedPowerProduction

# step defines the time interval between each data point
step: dt.timedelta = dt.timedelta(minutes=15)


class Client(models.DatabaseInterface):
    """Defines a dummy database that conforms to the DatabaseInterface."""

    @override
    async def get_predicted_solar_power_production_for_location(
        self,
        location: str,
        forecast_horizon: models.ForecastHorizon = models.ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[models.PredictedPower]:
        # Get the window
        start, end = get_window()
        numSteps = int((end - start) / step)
        values: list[models.PredictedPower] = []

        for i in range(numSteps):
            time = start + i * step
            _PowerProduction = _basicSolarPowerProductionFunc(int(time.timestamp()))
            values.append(
                models.PredictedPower(
                    Time=time,
                    PowerKW=int(_PowerProduction.PowerProductionKW),
                    CreatedTime=dt.datetime.now(tz=dt.UTC),
                ),
            )

        return values

    @override
    async def get_predicted_wind_power_production_for_location(
        self,
        location: str,
        forecast_horizon: models.ForecastHorizon = models.ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[models.PredictedPower]:
        # Get the window
        start, end = get_window()
        numSteps = int((end - start) / step)
        values: list[models.PredictedPower] = []

        for i in range(numSteps):
            time = start + i * step
            _PowerProduction = _basicWindPowerProductionFunc()
            values.append(
                models.PredictedPower(
                    Time=time,
                    PowerKW=int(_PowerProduction.PowerProductionKW),
                    CreatedTime=dt.datetime.now(tz=dt.UTC),
                ),
            )

        return values

    @override
    async def get_actual_solar_power_production_for_location(
        self,
        location: str,
    ) -> list[models.ActualPower]:
        # Get the window
        start, end = get_window()
        numSteps = int((end - start) / step)
        values: list[models.ActualPower] = []

        for i in range(numSteps):
            time = start + i * step
            _PowerProduction = _basicSolarPowerProductionFunc(int(time.timestamp()))
            values.append(
                models.ActualPower(
                    Time=time,
                    PowerKW=int(_PowerProduction.PowerProductionKW),
                ),
            )

        return values

    @override
    async def get_actual_wind_power_production_for_location(
        self,
        location: str,
    ) -> list[models.ActualPower]:
        # Get the window
        start, end = get_window()
        numSteps = int((end - start) / step)
        values: list[models.ActualPower] = []

        for i in range(numSteps):
            time = start + i * step
            _PowerProduction = _basicWindPowerProductionFunc()
            values.append(
                models.ActualPower(
                    Time=time,
                    PowerKW=int(_PowerProduction.PowerProductionKW),
                ),
            )

        return values

    @override
    async def get_wind_regions(self) -> list[str]:
        return ["dummy_wind_region1", "dummy_wind_region2"]

    @override
    async def get_solar_regions(self) -> list[str]:
        return ["dummy_solar_region1", "dummy_solar_region2"]

    @override
    async def save_api_call_to_db(self, url: str, authdata: dict[str, str]) -> None:
        pass

    @override
    async def get_sites(self, authdata: dict[str, str]) -> list[models.Site]:
        site = models.Site(
            site_uuid=uuid4(),
            client_site_name="Dummy Site",
            latitude=26,
            longitude=76,
            capacity_kw=76,
            orientation=180,
            tilt=30,
        )

        return [site]

    @override
    async def put_site(
        self,
        site_uuid: UUID,
        site_properties: models.SiteProperties,
        authdata: dict[str, str],
    ) -> models.Site:
        sites = await self.get_sites(authdata=authdata)
        return sites[0]

    @override
    async def get_site_forecast(
        self,
        site_uuid: UUID,
        authdata: dict[str, str],
    ) -> list[models.PredictedPower]:
        values = await self.get_predicted_solar_power_production_for_location(location="dummy")
        return values

    @override
    async def get_site_generation(
        self,
        site_uuid: UUID,
        authdata: dict[str, str],
    ) -> list[models.ActualPower]:
        values = await self.get_actual_solar_power_production_for_location(location="dummy")
        return values

    @override
    async def post_site_generation(
        self,
        site_uuid: UUID,
        generation: list[models.ActualPower],
        authdata: dict[str, str],
    ) -> None:
        pass

    @override
    async def get_substations(
        self,
        authdata: dict[str, str],
    ) -> list[models.Substation]:
        sub = models.Substation(
            substation_uuid=uuid4(),
            substation_name="Dummy Substation",
            substation_type="primary",
            latitude=26,
            longitude=76,
            capacity_kw=76,
        )

        return [sub]

    @override
    async def get_substation(
        self,
        location_uuid: UUID,
        authdata: dict[str, str],
    ) -> models.SubstationProperties:
        return models.SubstationProperties(
            substation_name="Dummy Substation",
            substation_type="primary",
            latitude=26,
            longitude=76,
            capacity_kw=76,
        )

    @override
    async def get_substation_forecast(
        self,
        location_uuid: UUID,
        authdata: dict[str, str],
    ) -> list[models.PredictedPower]:
        values = await self.get_predicted_solar_power_production_for_location(location="dummy")

        return values


def _basicSolarPowerProductionFunc(
    timeUnix: int,
    scaleFactor: int = 10000,
) -> DummyDBPredictedPowerProduction:
    """Gets a fake solar PowerProduction for the input time.

    The basic PowerProduction function is built from a sine wave
    with a period of 24 hours, peaking at 12 hours.
    Further convolutions modify the value according to time of year.

    Args:
        timeUnix: The time in unix time.
        scaleFactor: The scale factor for the sine wave.
            A scale factor of 10000 will result in a peak PowerProduction of 10 kW.
    """
    # Create a datetime object from the unix time
    time = dt.datetime.fromtimestamp(timeUnix, tz=dt.UTC)
    # The functions x values are hours, so convert the time to hours
    hour = time.day * 24 + time.hour + time.minute / 60 + time.second / 3600

    # scaleX makes the period of the function 24 hours
    scaleX = math.pi / 12
    # translateX moves the minimum of the function to 0 hours
    translateX = -math.pi / 2
    # translateY modulates the base function based on the month.
    # * + 0.5 at the summer solstice
    # * - 0.5 at the winter solstice
    translateY = math.sin((math.pi / 6) * time.month + translateX) / 2.0

    # basefunc ranges between -1 and 1 with a period of 24 hours,
    # peaking at 12 hours.
    # translateY changes the min and max to range between 1.5 and -1.5
    # depending on the month.
    basefunc = math.sin(scaleX * hour + translateX) + translateY
    # Remove negative values
    basefunc = max(0, basefunc)
    # Steepen the curve. The divisor is based on the max value
    basefunc = basefunc**4 / 1.5**4

    # Instead of completely random noise, apply based on the following process:
    # * A base noise function which is the product of long and short sines
    # * The resultant function modulates with very small amplitude around 1
    noise = (math.sin(math.pi * time.hour) / 20) * (math.sin(math.pi * time.hour / 3)) + 1
    noise = noise * random.random() / 20 + 0.97

    # Create the output value from the base function, noise, and scale factor
    output = basefunc * noise * scaleFactor

    # Add some random Uncertaintyor
    UncertaintyLow: float = 0.0
    UncertaintyHigh: float = 0.0
    if output > 0:
        UncertaintyLow = output - (random.random() * output / 10)
        UncertaintyHigh = output + (random.random() * output / 10)

    return DummyDBPredictedPowerProduction(
        PowerProductionKW=output,
        UncertaintyLow=UncertaintyLow,
        UncertaintyHigh=UncertaintyHigh,
    )


def _basicWindPowerProductionFunc(
    scaleFactor: int = 10000,
) -> DummyDBPredictedPowerProduction:
    """Gets a fake wind PowerProduction for the input time."""
    output = min(scaleFactor, scaleFactor * 10 * random.random())

    UncertaintyLow: float = 0.0
    UncertaintyHigh: float = 0.0
    if output > 0:
        UncertaintyLow = output - (random.random() * output / 10)
        UncertaintyHigh = output + (random.random() * output / 10)

    return DummyDBPredictedPowerProduction(
        PowerProductionKW=output,
        UncertaintyLow=UncertaintyLow,
        UncertaintyHigh=UncertaintyHigh,
    )
