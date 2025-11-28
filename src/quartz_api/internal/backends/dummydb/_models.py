import dataclasses as dc


@dc.dataclass
class DummyDBPredictedPowerProduction:
    """Structure of the predicted Power Production data from the dummy database."""

    PowerProductionKW: float
    UncertaintyLow: float
    UncertaintyHigh: float
