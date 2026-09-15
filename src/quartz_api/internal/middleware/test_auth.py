import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from quartz_api.internal.middleware.auth import AuthClient, get_org_id_from_authdata


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
