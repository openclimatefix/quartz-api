"""A data platform implementation that conforms to the DatabaseInterface."""
import datetime as dt
import math
import random
from uuid import uuid4
from typing import Optional


from quartz_api import internal
from quartz_api.internal.models import ForecastHorizon

from dp_sdk.ocf import dp

from ..utils import get_window


class Client(internal.DatabaseInterface):
    """Defines a data platform interface that conforms to the DatabaseInterface."""

    c: dp.DataPlatformDataServiceStub

    def get_predicted_solar_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: Optional[int] = None,
    ) -> list[internal.PredictedPower]:
        """Overrides parent method."""
        start, end = get_window()

