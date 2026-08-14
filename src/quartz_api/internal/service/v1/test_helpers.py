"""Unit tests for the trial-expiry helper in v1 helpers."""

import datetime as dt

from .helpers import trial_expired


def test_trial_expired_true_when_claim_in_past() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    auth = {"app_metadata": {"trial_ends_at": "2025-12-31T00:00:00.000Z"}}
    assert trial_expired(auth, now) is True


def test_trial_expired_false_when_claim_missing_or_future() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    assert trial_expired({}, now) is False
    auth = {"app_metadata": {"trial_ends_at": "2026-06-01T00:00:00.000Z"}}
    assert trial_expired(auth, now) is False
