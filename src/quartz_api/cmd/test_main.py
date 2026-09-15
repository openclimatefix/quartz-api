"""Tests for the server assembly in main.py."""

import grpc
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyhocon import ConfigFactory

from quartz_api.cmd.main import _create_v1_app

_DP_DETAILS = "no such forecaster: no rows in result set"


def _app_raising(code: grpc.StatusCode, details: str = _DP_DETAILS) -> FastAPI:
    """A v1 app with one route that fails the way an uncaught DP call would."""
    app = _create_v1_app(ConfigFactory.parse_string("{}"), None)

    @app.get("/boom")
    async def boom() -> None:
        raise grpc.aio.AioRpcError(
            code,
            grpc.aio.Metadata(),
            grpc.aio.Metadata(),
            details=details,
            debug_error_string="UNKNOWN:Error received from peer {grpc_status:5}",
        )

    return app


def test_grpc_not_found_becomes_404() -> None:
    """A DP NOT_FOUND reaches the caller as a 404 carrying the platform's own reason."""
    resp = TestClient(_app_raising(grpc.StatusCode.NOT_FOUND)).get("/boom")
    assert resp.status_code == 404
    assert resp.json()["detail"] == _DP_DETAILS


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.INVALID_ARGUMENT, 400),
        (grpc.StatusCode.PERMISSION_DENIED, 403),
        (grpc.StatusCode.UNAVAILABLE, 503),
        (grpc.StatusCode.DEADLINE_EXCEEDED, 504),
        (grpc.StatusCode.INTERNAL, 502),
    ],
)
def test_grpc_status_mapping(code: grpc.StatusCode, expected: int) -> None:
    """Each gRPC status the DP can answer with maps onto an HTTP status."""
    resp = TestClient(_app_raising(code)).get("/boom")
    assert resp.status_code == expected


@pytest.mark.parametrize(
    "code",
    [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.INTERNAL],
)
def test_grpc_server_errors_stay_generic(code: grpc.StatusCode) -> None:
    """A 5xx says nothing about the platform internals; the detail stays in the log."""
    resp = TestClient(_app_raising(code)).get("/boom")
    assert _DP_DETAILS not in resp.text


def test_grpc_error_repr_never_reaches_the_caller() -> None:
    """The debug string names the peer, so it must not travel with the response."""
    resp = TestClient(_app_raising(grpc.StatusCode.NOT_FOUND)).get("/boom")
    assert "debug_error_string" not in resp.text
    assert "AioRpcError" not in resp.text
