import pandas as pd
from quartz_api.internal import (
    ActualPower
)


def resample_generation(values: list[ActualPower], internal_minutes: int) -> list[ActualPower]:
    if not values:
        return []

    # convert to dataframe
    df = pd.DataFrame(
        {
            "Time": [value.Time for value in values],
            "PowerKW": [value.PowerKW for value in values],
        }
    )

    # resample
    df = df.set_index("Time").resample(f"{internal_minutes}T").mean().dropna()

    df['PowerKW'].clip(lower=0, inplace=True)  # Set negative values of PowerKW up to 0


    # convert back to list of ActualPower
    return [
        ActualPower(
            Time=index,
            PowerKW=row.PowerKW,
        )
        for index, row in df.iterrows()
    ]