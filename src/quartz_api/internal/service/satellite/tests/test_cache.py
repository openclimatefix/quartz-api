"""Unit tests for satellite presigned-URL cache warming and lookup."""

import datetime as dt
import json
import typing
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from httpx import ASGITransport, AsyncClient

from quartz_api.internal.middleware.auth import AuthDependency, auth_instance
from quartz_api.internal.s3 import S3Client, get_s3_client

from ..cache import _presign_period_base_key, satellite_cache_warming
from ..router import router

auth_instance.instantiate_dummy()
_auth_dep = typing.get_args(AuthDependency)[1].dependency


class FakeS3Client:
    """Stub standing in for S3Client: only ``present`` keys "exist"."""

    def __init__(self, present: set[str]) -> None:
        self.present = present
        self.object_exists_calls = 0
        self.get_presigned_url_calls = 0

    def object_exists(self, bucket: str, key: str) -> bool:  # noqa: ARG002
        self.object_exists_calls += 1
        return key in self.present

    def get_presigned_url(self, bucket: str, key: str, expiration: int = 3600) -> str:  # noqa: ARG002
        self.get_presigned_url_calls += 1
        return f"https://example.test/{key}?expires={expiration}"

    def get_latest_key(
        self, bucket: str, prefix: str, lookback_minutes: int = 30,
    ) -> str | None:
        ts = dt.datetime.now(tz=dt.UTC).replace(second=0, microsecond=0)
        ts -= dt.timedelta(minutes=ts.minute % 5)
        for _ in range(lookback_minutes // 5 + 1):
            key = f"{prefix}{ts:%Y%m%d_%H%M%S}.tif"
            if self.object_exists(bucket, key):
                return key
            ts -= dt.timedelta(minutes=5)
        return None


@pytest_asyncio.fixture(autouse=True)
async def clear_cache() -> AsyncGenerator[None]:
    """FastAPICache.init() no-ops once initialised, so the store is shared between
    tests. Clear it (and the warming-guard dict) so state doesn't leak between
    tests."""
    FastAPICache.init(InMemoryBackend(), prefix="test")
    await FastAPICache.clear()
    satellite_cache_warming.clear()
    yield
    await FastAPICache.clear()
    satellite_cache_warming.clear()


def _make_app(s3: S3Client) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_s3_client] = lambda: s3
    app.dependency_overrides[_auth_dep] = lambda: {"sub": "test|user", "permissions": []}
    return app


@pytest_asyncio.fixture
async def s3() -> FakeS3Client:
    return FakeS3Client(present=set())


@pytest_asyncio.fixture
async def client(s3: FakeS3Client) -> AsyncGenerator[AsyncClient]:
    app = _make_app(s3)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _seed_cache(channel: str, entries: dict[str, dict]) -> None:
    backend = FastAPICache.get_backend()
    prefix = FastAPICache.get_prefix()
    base = _presign_period_base_key(prefix, channel)
    await backend.set(f"{base}:entries", json.dumps(entries), expire=60)
    await backend.set(f"{base}:_meta", json.dumps({"count": len(entries)}), expire=60)


@pytest.mark.asyncio
async def test_push_entry_to_cache() -> None:
    """Simple write: seeding the cache actually stores the entry."""
    ts = dt.datetime(2026, 1, 1, 0, 30, tzinfo=dt.UTC)
    await _seed_cache("VIS006", {ts.isoformat(): {"url": "https://example.test/pushed"}})

    backend = FastAPICache.get_backend()
    prefix = FastAPICache.get_prefix()
    base = _presign_period_base_key(prefix, "VIS006")

    raw_entries = await backend.get(f"{base}:entries")
    assert raw_entries is not None
    entries = json.loads(raw_entries)
    assert entries[ts.isoformat()]["url"] == "https://example.test/pushed"


@pytest.mark.asyncio
async def test_read_entry_from_cache(client: AsyncClient) -> None:
    """Simple read: an entry already in the cache comes back via the API."""
    ts = dt.datetime(2026, 1, 1, 0, 30, tzinfo=dt.UTC)
    await _seed_cache("VIS006", {ts.isoformat(): {"url": "https://example.test/from-cache"}})

    resp = await client.get(
        "/satellite/",
        params={"channel": "VIS006", "timestamp": ts.isoformat()},
    )
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://example.test/from-cache"
