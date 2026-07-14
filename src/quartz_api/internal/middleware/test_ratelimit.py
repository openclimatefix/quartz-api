"""Tests for rate limiting utilities."""
import typing

import jwt
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pyhocon import ConfigFactory
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from quartz_api.cmd.main import _create_v1_app
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.middleware.ratelimit import get_user_key


def _make_app(rate_limit: str = "1/second") -> FastAPI:
    """Create a minimal FastAPI app with the given rate limit applied to a test route."""
    test_limiter = Limiter(key_func=get_user_key)
    app = FastAPI()
    app.state.limiter = test_limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.get("/test")
    @test_limiter.limit(rate_limit)
    async def _route(request: Request) -> dict:  # noqa: ARG001
        return {"ok": True}

    return app


def test_rate_limit_second_request_returns_429() -> None:
    """Two rapid requests from the same client should trigger a 429 on the second."""
    client = TestClient(_make_app("1/second"), raise_server_exceptions=False)

    first = client.get("/test")
    assert first.status_code == 200

    second = client.get("/test")
    assert second.status_code == 429


def test_rate_limit_with_jwt_same_user_returns_429() -> None:
    """Two rapid requests with the same JWT sub should trigger a 429 on the second."""
    token = jwt.encode({"sub": "user|abc123"}, "secret", algorithm="HS256")
    client = TestClient(_make_app("1/second"), raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.get("/test", headers=headers)
    assert first.status_code == 200

    second = client.get("/test", headers=headers)
    assert second.status_code == 429


def test_get_user_key_falls_back_to_ip_without_auth() -> None:
    """get_user_key should return an IP address when no Authorization header is present."""
    app = FastAPI()

    @app.get("/key")
    async def _capture_key(request: Request) -> dict:
        return {"key": get_user_key(request)}

    client = TestClient(app)
    response = client.get("/key")
    assert response.status_code == 200
    # The TestClient uses "testclient" as the remote address
    assert response.json()["key"] == "testclient"


def test_get_user_key_uses_sub_from_valid_jwt() -> None:
    """get_user_key should extract the 'sub' claim from a valid JWT."""
    token = jwt.encode({"sub": "user|abc123"}, "secret", algorithm="HS256")

    app = FastAPI()

    @app.get("/key")
    async def _capture_key(request: Request) -> dict:
        return {"key": get_user_key(request)}

    client = TestClient(app)
    response = client.get("/key", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["key"] == "user|abc123"


def test_get_user_key_falls_back_to_ip_on_invalid_jwt() -> None:
    """get_user_key should fall back to IP when the JWT cannot be decoded."""
    app = FastAPI()

    @app.get("/key")
    async def _capture_key(request: Request) -> dict:
        return {"key": get_user_key(request)}

    client = TestClient(app)
    response = client.get("/key", headers={"Authorization": "Bearer not.a.real.jwt"})
    assert response.status_code == 200
    assert response.json()["key"] == "testclient"


def test_v1_app_rate_limiting() -> None:
    """Test that rate limiting is applied to v1 sub-app routes.

    Uses a local Limiter instance (1/second) to avoid mutating the global
    singleton and keep the test fully isolated.
    """
    conf = ConfigFactory.parse_string('auth0 { client_id = "test_client" }')
    v1_app = _create_v1_app(conf, None)

    # Override the auth dependency so it bypasses auth signature verification
    auth_dep = typing.get_args(AuthDependency)[1].dependency
    v1_app.dependency_overrides[auth_dep] = lambda: {"sub": "test_user_rate_limit"}

    # Replace the app-level limiter with a local restrictive one (1/second)
    # so we don't mutate the shared global singleton or rely on LimitGroup internals.
    local_limiter = Limiter(
        key_func=get_user_key,
        default_limits=["1/second"],
        key_style="endpoint",
    )
    v1_app.state.limiter = local_limiter

    client = TestClient(v1_app, raise_server_exceptions=False)

    # First request should succeed
    r1 = client.get("/sources")
    assert r1.status_code == 200

    # Second request within the same second should trigger 429
    r2 = client.get("/sources")
    assert r2.status_code == 429

