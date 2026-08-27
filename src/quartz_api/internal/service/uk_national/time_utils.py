"""Utility functions for handling datetime objects in UK National context."""

import datetime as dt
import os

import sentry_sdk

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

# TODO this would be nice if this was done with the high level quartz-api config file
# One idea is we could put end_datetime_utc with auth into some middleware,
# and the time clipping is done then
INTRADAY_LIMIT_HOURS = int(os.getenv("INTRADAY_LIMIT_HOURS", 8))

def trial_expired(auth: AuthDependency, now: dt.datetime) -> bool:
    """Check whether the caller's trial has ended, per the app_metadata.trial_ends_at claim.

    No claim at all (paid/non-trial user) is treated the same as an unexpired
    trial: both mean "don't clamp." Only a claim that has actually passed
    counts as expired.
    """
    app_metadata = auth.get("app_metadata", {})
    if not isinstance(app_metadata, dict):
        return False
    raw = app_metadata.get("trial_ends_at")
    if raw is None:
        return False
    try:
        ends_at = dt.datetime.fromisoformat(str(raw))
    except ValueError:
        sentry_sdk.capture_message(f"Unparseable trial_ends_at claim: {raw!r}")
        return False
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=dt.UTC)
    return ends_at <= now


def limit_end_datetime_by_permissions(
    auth: AuthDependency,
    end_datetime_utc: models.UTCDatetimeDefaultWindowEndNonAware,
) -> dt.datetime:
    """Ensures only users with required permissions/an active trial can see future data."""
    permissions: str | list[str] = auth.get("permissions", [])
    if isinstance(permissions, str):
        permissions = [permissions]
    if not permissions or len(permissions) == 0:
        sentry_sdk.capture_message(
            "User has no permissions during limit_end_datetime_by_permissions check;"
            "by default, users should have at least one role, so check in Auth0.",
        )
        return end_datetime_utc

    intraday_max_allowed = dt.datetime.now(dt.UTC) + dt.timedelta(hours=INTRADAY_LIMIT_HOURS)
    if trial_expired(auth, dt.datetime.now(dt.UTC)):
        return min(end_datetime_utc, dt.datetime.now(dt.UTC))
    if "read:uk-intraday" in permissions:
        return min(end_datetime_utc, intraday_max_allowed)

    return end_datetime_utc
