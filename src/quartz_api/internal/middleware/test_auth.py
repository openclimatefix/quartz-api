import datetime as dt
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from quartz_api.internal.middleware.auth import (
    AuthClient,
    get_org_id_from_authdata,
    trial_expired,
)


def test_get_org_id_from_authdata_regular_user() -> None:
    authdata = {
        "app_metadata": {
            "hubspot_company_id": "12345",
        },
        "permissions": ["read:india"],
    }
    assert get_org_id_from_authdata(authdata) == "12345"

def test_get_org_id_from_authdata_admin_user() -> None:
    authdata = {
        "app_metadata": {
            "hubspot_company_id": "99999999999",
        },
        "permissions": ["ocf:admin", "read:india"],
    }
    assert get_org_id_from_authdata(authdata) is None

def test_get_org_id_from_authdata_no_metadata() -> None:
    authdata = {
        "permissions": ["read:india"],
    }
    assert get_org_id_from_authdata(authdata) == "no-org-access"



@pytest.fixture
def auth0_app() -> FastAPI:
    """An app wired to the real Auth0 backend, which needs no network until it verifies."""
    client = AuthClient()
    client.instantiate_auth0(domain="example.eu.auth0.com", audience="https://api.example")
    app = FastAPI()

    @app.get("/probe")
    async def _probe(auth: dict = Depends(client.require_auth())) -> dict:  # noqa: ARG001, B008
        return {"ok": True}

    return app


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer   "},
        {"Authorization": "Basic aGk6dGhlcmU="},
    ],
    ids=["absent", "empty", "no-token", "blank-token", "wrong-scheme"],
)
def test_missing_credentials_are_401(auth0_app: FastAPI, headers: dict) -> None:
    """A request with no usable bearer token is a 401 with a challenge, not a 400."""
    resp = TestClient(auth0_app).get("/probe", headers=headers)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_a_present_token_is_left_to_the_plugin(auth0_app: FastAPI) -> None:
    """A token that is present but bad must reach the verifier, not our shortcut.

    It answers 401 too, but as `invalid_token` with its own challenge. The distinction
    matters: only the credential-shaped cases are ours to answer.
    """
    resp = TestClient(auth0_app).get("/probe", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401
    assert "invalid_token" in resp.headers["www-authenticate"]


def test_dummy_backend_is_untouched() -> None:
    """The check must not apply to the dummy backend, which takes no token at all."""
    client = AuthClient()
    client.instantiate_dummy()
    app = FastAPI()

    @app.get("/probe")
    async def _probe(auth: dict = Depends(client.require_auth())) -> dict:  # noqa: ARG001, B008
        return {"ok": True}

    assert TestClient(app).get("/probe").status_code == 200


def test_trial_expired_true_when_claim_in_past() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    auth = {"permissions": ["read:trial"], "trial_ends_at": "2025-12-31T00:00:00.000Z"}
    assert trial_expired(auth, now) is True


def test_trial_expired_paying_client_with_stale_past_claim_not_expired() -> None:
    """A trial that converted to paid keeps its old date on the token, but isn't clamped."""
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    auth = {"permissions": ["read:gb"], "trial_ends_at": "2025-12-31T00:00:00.000Z"}
    assert trial_expired(auth, now) is False


def test_trial_expired_false_when_claim_missing_or_future() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    assert trial_expired({}, now) is False
    auth = {"trial_ends_at": "2026-06-01T00:00:00.000Z"}
    assert trial_expired(auth, now) is False


def test_trial_expired_unparseable_claim_logs_and_reports() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    auth = {"permissions": ["read:trial"], "trial_ends_at": "not-a-date"}
    with patch("quartz_api.internal.middleware.auth.sentry_sdk.capture_message") as capture:
        assert trial_expired(auth, now) is True
    capture.assert_called_once()


def test_trial_expired_ignores_stale_app_metadata_copy() -> None:
    """A trial extended today: the claim is in the future, app_metadata still in the past."""
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    auth = {
        "trial_ends_at": "2026-02-01T00:00:00.000Z",
        "app_metadata": {"trial_ends_at": "2025-12-31T00:00:00.000Z"},
    }
    assert trial_expired(auth, now) is False


def test_trial_expired_trial_user_without_usable_date_counts_as_expired() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    assert trial_expired({"permissions": ["read:trial"]}, now) is True
    assert trial_expired({"permissions": "read:trial"}, now) is True
    with patch("quartz_api.internal.middleware.auth.sentry_sdk.capture_message"):
        auth = {"permissions": ["read:trial"], "trial_ends_at": "not-a-date"}
        assert trial_expired(auth, now) is True


def test_trial_expired_trial_user_with_future_date_not_expired() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    auth = {"permissions": ["read:trial"], "trial_ends_at": "2026-06-01T00:00:00.000Z"}
    assert trial_expired(auth, now) is False


def test_trial_expired_paying_client_without_claim_not_expired() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    assert trial_expired({"permissions": ["read:gb", "read:uk-intraday"]}, now) is False
