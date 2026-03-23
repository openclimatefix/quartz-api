"""A dummy database that conforms to the DatabaseInterface."""
# ruff: noqa: S311

import dataclasses
import datetime as dt
import logging
import math
import random
from uuid import UUID, uuid4

from typing_extensions import override

from quartz_api.internal import models

step: dt.timedelta = dt.timedelta(minutes=15)

log = logging.getLogger("dummydb")


class StorageClient(models.StorageInterface):
    """Defines a dummy storage that conforms to the StorageInterface."""

    @override
    async def get_predicted_generation(
        self,
        location_uuid: UUID | str,
        window_start: dt.datetime,
        window_end: dt.datetime,
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
        created_cutoff: dt.datetime | None = None,
        forecast_horizon_minutes: int = 0,
        forecaster_name: str | None = None,
        forecaster_version: str | None = None,
    ) -> list[models.PredictedGenerationValue]:
        numSteps = int((window_end - window_start) / step)
        values: list[models.PredictedGenerationValue] = []

        if isinstance(location_uuid, str):
            location_uuid = uuid4()

        for i in range(numSteps):
            t = window_start + i * step
            match energy_type:
                case models.EnergyType.WIND:
                    pf = _basicWindPowerProductionFunc()
                case models.EnergyType.SOLAR:
                    pf = _basicSolarPowerProductionFunc(int(t.timestamp()))

            values.append(
                models.PredictedGenerationValue(
                    power_kilowatts=pf.PowerProductionKW,
                    valid_timestamp=t,
                    location_uuid=location_uuid,
                    capacity_kilowatts=10000,
                    created_timestamp=window_start - dt.timedelta(hours=1),
                    init_timestamp=window_start,
                    forecaster_name=forecaster_name or "DummyForecaster",
                    forecaster_version=forecaster_version or "0.0.0",
                    plevels_kilowatts={"p10": pf.UncertaintyLow, "p90": pf.UncertaintyHigh},
                ),
            )

        return values

    @override
    async def put_predicted_generation(
        self,
        generation_values: list[models.PredictedGenerationValue],
        energy_type: models.EnergyType,
        authdata: dict[str, str],
    ) -> None:
        pass

    @override
    async def get_actual_generation(
        self,
        location_uuid: UUID | str,
        window_start: dt.datetime,
        window_end: dt.datetime,
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
        created_cutoff: dt.datetime | None = None,
        observer_name: str | None = None,
    ) -> list[models.ActualGenerationValue]:
        numSteps = int((window_end - window_start) / step)
        values: list[models.ActualGenerationValue] = []

        if isinstance(location_uuid, str):
            location_uuid = uuid4()

        for i in range(numSteps):
            t = window_start + i * step
            match energy_type:
                case models.EnergyType.WIND:
                    pf = _basicWindPowerProductionFunc()
                case models.EnergyType.SOLAR:
                    pf = _basicSolarPowerProductionFunc(int(t.timestamp()))

            values.append(
                models.ActualGenerationValue(
                    power_kilowatts=pf.PowerProductionKW,
                    valid_timestamp=t,
                    location_uuid=location_uuid,
                    capacity_kilowatts=10000,
                    observer_name=observer_name or "DummyObserver",
                ),
            )

        return values

    @override
    async def put_actual_generation(
        self,
        generation_values: list[models.ActualGenerationValue],
        location_uuid: UUID,
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
    ) -> None:
        pass

    @override
    async def get_locations(
        self,
        energy_type: models.EnergyType,
        location_type: models.LocationType,
        authdata: dict[str, str],
        location_uuid: UUID | None = None,
    ) -> list[models.Location]:
        loc_uuid = location_uuid or uuid4()
        match energy_type, location_type:
            case models.EnergyType.SOLAR, models.LocationType.REGION:
                return [
                    models.Location(
                        uuid=loc_uuid,
                        name=n,
                        latitude=26,
                        longitude=76,
                        capacity_kilowatts=76000,
                    )
                    for n in ["dummy_solar_region1", "dummy_solar_region2"]
                ]
            case models.EnergyType.WIND, models.LocationType.REGION:
                return [
                    models.Location(
                        uuid=loc_uuid,
                        name=n,
                        latitude=36,
                        longitude=86,
                        capacity_kilowatts=86000,
                    )
                    for n in ["dummy_wind_region1", "dummy_wind_region2"]
                ]
            case _, models.LocationType.SITE:
                return [
                    models.Location(
                        uuid=loc_uuid,
                        name="Dummy Site",
                        latitude=26,
                        longitude=76,
                        capacity_kilowatts=76000,
                    ),
                ]
            case _, models.LocationType.GSP:
                return [
                    models.Location(
                        uuid=loc_uuid,
                        name="Dummy GSP",
                        latitude=26,
                        longitude=76,
                        capacity_kilowatts=76000,
                        metadata={"gsp_id": 12345},
                    ),
                ]
            case _, models.LocationType.SUBSTATION:
                return [
                    models.Location(
                        uuid=loc_uuid,
                        name="Dummy Substation",
                        latitude=26,
                        longitude=76,
                        capacity_kilowatts=76000,
                        metadata={"gsp_id": 12345},
                    ),
                ]
            case _:
                raise NotImplementedError(
                    f"DummyDB client does not support {location_type} locations"
                    f"for {energy_type} energy type.",
                )

    @override
    async def get_predicted_generation_snapshot(
        self,
        location_uuids: list[UUID],
        snapshot_timestamp_utc: dt.datetime,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
        forecaster_name: str | None = None,
        forecaster_version: str | None = None,
    ) -> list[models.PredictedGenerationValue]:
        values: list[models.PredictedGenerationValue] = []
        for location_uuid in location_uuids:
            match energy_type:
                case models.EnergyType.WIND:
                    pf = _basicWindPowerProductionFunc()
                case models.EnergyType.SOLAR:
                    pf = _basicSolarPowerProductionFunc(int(snapshot_timestamp_utc.timestamp()))

            values.append(
                models.PredictedGenerationValue(
                    power_kilowatts=pf.PowerProductionKW,
                    valid_timestamp=snapshot_timestamp_utc,
                    location_uuid=location_uuid,
                    capacity_kilowatts=10000,
                    created_timestamp=snapshot_timestamp_utc - dt.timedelta(hours=1),
                    init_timestamp=snapshot_timestamp_utc,
                    forecaster_name=forecaster_name or "DummyForecaster",
                    forecaster_version=forecaster_version or "0.0.0",
                    plevels_kilowatts={"p10": pf.UncertaintyLow, "p90": pf.UncertaintyHigh},
                ),
            )
        return values

    @override
    async def get_actual_generation_snapshot(
        self,
        location_uuids: list[UUID],
        snapshot_timestamp_utc: dt.datetime,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
        observer_name: str | None = None,
    ) -> list[models.ActualGenerationValue]:
        values: list[models.ActualGenerationValue] = []
        for location_uuid in location_uuids:
            match energy_type:
                case models.EnergyType.WIND:
                    pf = _basicWindPowerProductionFunc()
                case models.EnergyType.SOLAR:
                    pf = _basicSolarPowerProductionFunc(int(snapshot_timestamp_utc.timestamp()))

            values.append(
                models.ActualGenerationValue(
                    power_kilowatts=pf.PowerProductionKW,
                    valid_timestamp=snapshot_timestamp_utc,
                    location_uuid=location_uuid,
                    capacity_kilowatts=10000,
                    observer_name=observer_name or "DummyObserver",
                ),
            )
        return values

    @override
    async def put_location(
        self,
        location: models.Location,
        location_type: models.LocationType,
        energy_type: models.EnergyType,
        authdata: dict[str, str],
    ) -> models.Location:
        """Store or update a location in the storage system."""
        match energy_type, location_type:
            case _, models.LocationType.SITE:
                return location
            case _:
                raise NotImplementedError(
                    f"DummyDB client does not support storing {location_type} locations"
                    f"for {energy_type} energy type.",
                )

    @override
    async def log_api_call(
        self,
        url: str,
        authdata: dict[str, str],
    ) -> None:
        """Log an API call to the storage system."""
        log.info(f"LOG: Call to '{url}'")
        pass


@dataclasses.dataclass
class DummyDBPredictedPowerProduction:
    """Structure of the predicted Power Production data from the dummy database."""

    PowerProductionKW: float
    UncertaintyLow: float
    UncertaintyHigh: float


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
