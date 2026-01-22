"""Utility functions for handling datetime objects."""
import datetime as dt

import pandas as pd


def get_start_window() ->  dt.datetime:
    """Returns the start of the window for timeseries data."""
    return pd.Timestamp.utcnow().floor("6h").to_pydatetime() - dt.timedelta(days=2)

def get_end_window() ->  dt.datetime:
    """Returns the end of the window for timeseries data."""
    return pd.Timestamp.utcnow().floor("6h").to_pydatetime() + dt.timedelta(days=2)

def get_now_floor_30_mins() ->  dt.datetime:
    """Returns the current time rounded down to the nearest 30 minutes."""
    return pd.Timestamp.utcnow().floor("30T").to_pydatetime()



