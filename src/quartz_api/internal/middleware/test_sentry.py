"""Tests for the Sentry traces sampler."""

import pytest

from quartz_api.internal.middleware.sentry import make_traces_sampler


def _ctx(path: str, method: str = "GET", parent: bool | None = None) -> dict:
    return {"asgi_scope": {"type": "http", "path": path, "method": method},
            "parent_sampled": parent}


@pytest.mark.parametrize(
    "path",
    ["/health", "/favicon.ico", "/docs", "/openapi.json", "/static/logo.png",
     "/v1/docs", "/v1/openapi.json"],
)
def test_noise_is_never_traced(path: str) -> None:
    assert make_traces_sampler(1.0)(_ctx(path)) == 0.0


def test_options_is_never_traced() -> None:
    assert make_traces_sampler(1.0)(_ctx("/v0/solar/GB/national/forecast", "OPTIONS")) == 0.0


def test_api_routes_use_configured_rate() -> None:
    assert make_traces_sampler(0.25)(_ctx("/v0/solar/GB/national/forecast")) == 0.25


def test_parent_decision_is_respected() -> None:
    sampler = make_traces_sampler(0.0)
    assert sampler(_ctx("/v0/solar/GB/national/forecast", parent=True)) == 1.0
    assert make_traces_sampler(1.0)(_ctx("/v0/solar/GB/national/forecast", parent=False)) == 0.0


def test_non_asgi_context_uses_rate() -> None:
    assert make_traces_sampler(0.5)({}) == 0.5
