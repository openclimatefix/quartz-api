""" Simple tests for enpoint types"""

import pandas as pd

from .endpoint_types import get_start_window_shifted_for_uk


def test_get_start_window_shifted_for_uk():
    """Test the get_start_window_shifted_for_uk function."""
    # 1. UK/London winter time
    result = get_start_window_shifted_for_uk(now=pd.Timestamp("2024-01-03T12:01:00Z"))
    assert result == pd.Timestamp("2024-01-01T12:00:00Z")

def test_get_start_window_shifted_for_uk_on_the_6_hour():
    # 2. UK/London winter time, on the 6 hours
    result = get_start_window_shifted_for_uk(now=pd.Timestamp("2024-01-03T12:00:00Z"))
    assert result == pd.Timestamp("2024-01-01T12:00:00Z")

def test_get_start_window_shifted_for_uk_summer():
    # 3. UK/London summer time
    result = get_start_window_shifted_for_uk(now=pd.Timestamp("2024-06-01T12:34:56Z"))
    assert result == pd.Timestamp("2024-05-30T11:00:00Z")

