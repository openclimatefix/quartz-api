"""A dummy database that conforms to the DatabaseInterface."""
import datetime as dt
import math
import random
from uuid import uuid4
from typing import Optional


from quartz_api import internal
from quartz_api.internal.models import ForecastHorizon

from ._models import DummyDBPredictedPowerProduction
from ..utils import get_window

# step defines the time interval between each data point
step: dt.timedelta = dt.timedelta(minutes=15)


class Client(internal.DatabaseInterface):
    """Defines a dummy database that conforms to the DatabaseInterface."""

    def get_predicted_solar_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: Optional[int] = None,
    ) -> list[internal.PredictedPower]:
        """Gets the predicted solar power production for a location.

        Args:
            location: The location to get the predicted solar power production for.
            forecast_horizon: The time horizon to get the data for. Can be latest or day ahead
            forecast_horizon_minutes: this is not used
        """
        # Get the window
        start, end = get_window()
        numSteps = int((end - start) / step)
        values: list[internal.PredictedPower] = []

        for i in range(numSteps):
            time = start + i * step
            _PowerProduction = _basicSolarPowerProductionFunc(int(time.timestamp()))
            values.append(
                internal.PredictedPower(
                    Time=time,
                    PowerKW=int(_PowerProduction.PowerProductionKW),
                    CreatedTime=dt.datetime.now(tz=dt.UTC),
                ),
            )

        return values

    def get_predicted_wind_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: Optional[int] = None,
    ) -> list[internal.PredictedPower]:
        """Gets the predicted wind power production for a location.

        Args:
            location: The location to get the predicted wind power production for.
            forecast_horizon: The time horizon to get the data for. Can be latest or day ahead
            forecast_horizon_minutes: this is not used but needed for function signature
        """
        # Get the window
        start, end = get_window()
        numSteps = int((end - start) / step)
        values: list[internal.PredictedPower] = []

        for i in range(numSteps):
            time = start + i * step
            _PowerProduction = _basicWindPowerProductionFunc(int(time.timestamp()))
            values.append(
                internal.PredictedPower(
                    Time=time,
                    PowerKW=int(_PowerProduction.PowerProductionKW),
                    CreatedTime=dt.datetime.now(tz=dt.UTC),
                ),
            )

        return values

    def get_actual_solar_power_production_for_location(
        self, location: str
    ) -> list[internal.ActualPower]:
        """Gets the actual solar power production for a location."""
        # Get the window
        start, end = get_window()
        numSteps = int((end - start) / step)
        values: list[internal.ActualPower] = []

        for i in range(numSteps):
            time = start + i * step
            _PowerProduction = _basicSolarPowerProductionFunc(int(time.timestamp()))
            values.append(
                internal.ActualPower(
                    Time=time,
                    PowerKW=int(_PowerProduction.PowerProductionKW),
                ),
            )

        return values

    def get_actual_wind_power_production_for_location(
        self, location: str
    ) -> list[internal.ActualPower]:
        """Gets the actual wind power production for a location."""
        # Get the window
        start, end = get_window()
        numSteps = int((end - start) / step)
        values: list[internal.ActualPower] = []

        for i in range(numSteps):
            time = start + i * step
            _PowerProduction = _basicWindPowerProductionFunc(int(time.timestamp()))
            values.append(
                internal.ActualPower(
                    Time=time,
                    PowerKW=int(_PowerProduction.PowerProductionKW),
                ),
            )

        return values

    def get_wind_regions(self) -> list[str]:
        """Gets the valid wind regions."""
        return ["dummy_wind_region1", "dummy_wind_region2"]

    def get_solar_regions(self) -> list[str]:
        """Gets the valid solar regions."""
        return ["dummy_solar_region1", "dummy_solar_region2"]

    def save_api_call_to_db(self, url: str, email=None):
        """Saves an API call to the database. This does nothing"""
        pass

    def get_sites(self, email: str) -> list[internal.Site]:
        """Get a list of sites"""

        uuid = str(uuid4())

        site = internal.Site(
            site_uuid=uuid,
            client_site_id=1,
            latitude=26,
            longitude=76,
            capacity_kw=76,
        )

        return [site]

    def put_site(
            self, site_uuid: str, site_properties: internal.SiteProperties, email: str
    ) -> internal.Site:
        pass

    def get_site_forecast(
        self, site_uuid: str, email: Optional[str] = None
    ) -> list[internal.PredictedPower]:
        """Get a forecast for a site, this is for a solar site"""

        values = self.get_predicted_solar_power_production_for_location(location="dummy")

        return values

    def get_site_generation(
        self, site_uuid: str, email: Optional[str] = None
    ) -> list[internal.ActualPower]:
        """Get the generation for a site, this is for a solar site"""

        values = self.get_actual_solar_power_production_for_location(location="dummy")

        return values

    def post_site_generation(
        self, site_uuid: str, generation: list[internal.ActualPower], email: Optional[str] = None
    ):
        """Post generation for a site"""
        pass


def _basicSolarPowerProductionFunc(
    timeUnix: int, scaleFactor: int = 10000
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
    basefunc = basefunc ** 4 / 1.5 ** 4

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
    timeUnix: int, scaleFactor: int = 10000
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
