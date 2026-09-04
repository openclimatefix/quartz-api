"""S3 client."""
from datetime import UTC, datetime, timedelta

import fsspec

# Satellite S3 config, populated at startup via configure() from the parsed
# HOCON schema (cmd/server.conf). server.conf is the single source of truth for
# values and defaults; this module just holds whatever it is handed.
_config: dict[str, str] | None = None
_s3_client: "S3Client | None" = None


def configure(
    *,
    region: str,
    geotiff_bucket: str,
    icechunk_bucket: str,
) -> None:
    """Set satellite S3 config from the parsed application config.

    Called once at startup from ``_create_server`` before routers are registered.
    """
    global _config, _s3_client
    _config = {
        "region": region,
        "geotiff_bucket": geotiff_bucket,
        "icechunk_bucket": icechunk_bucket,
    }
    # Reset the cached client so it picks up the new region.
    _s3_client = None


def _require_config() -> dict[str, str]:
    if _config is None:
        raise RuntimeError("s3.configure() must be called at startup before use")
    return _config


def get_geotiff_bucket() -> str:
    """Bucket holding the historic GeoTIFF layers served by the API."""
    return _require_config()["geotiff_bucket"]


def get_icechunk_bucket() -> str:
    """Bucket holding the Icechunk store."""
    return _require_config()["icechunk_bucket"]


def get_region() -> str:
    """AWS region for all satellite S3 operations."""
    return _require_config()["region"]


class S3Client:
    """S3 client."""

    def __init__(self) -> None:
        """Initialise S3 client."""
        region = _require_config()["region"]
        self.fs = fsspec.filesystem(
            "s3",
            **({"client_kwargs": {"region_name": region}} if region else {}),
        )

    def get_presigned_url(self, bucket: str, key: str, expiration: int = 3600) -> str:
        """Get a pre-signed URL for a file, valid for ``expiration`` seconds."""
        return self.fs.sign(f"s3://{bucket}/{key}", expiration=expiration)

    def object_exists(self, bucket: str, key: str) -> bool:
        """Check if an object exists in S3."""
        return self.fs.exists(f"s3://{bucket}/{key}")

    def get_latest_key(
        self, bucket: str, prefix: str, lookback_minutes: int = 30,
    ) -> str | None:
        """Search backwards in 5-minute intervals and return the first available key.

        Used to show the LIVE cloud image in the UI. The default 30-minute lookback
        keeps the image recent while remaining visually distinct.
        """
        ts = datetime.now(tz=UTC).replace(second=0, microsecond=0)
        ts -= timedelta(minutes=ts.minute % 5)
        for _ in range(lookback_minutes // 5 + 1):
            key = f"{prefix}{ts:%Y%m%d_%H%M%S}.tif"
            if self.object_exists(bucket, key):
                return key
            ts -= timedelta(minutes=5)
        return None

    def upload_bytes(self, bucket: str, key: str, data: bytes) -> None:
        """Upload raw bytes to an S3 key."""
        with self.fs.open(f"s3://{bucket}/{key}", "wb") as f:
            f.write(data)

    def download_bytes(self, bucket: str, key: str) -> bytes:
        """Download raw bytes from an S3 key."""
        with self.fs.open(f"s3://{bucket}/{key}", "rb") as f:
            return f.read()


def get_s3_client() -> S3Client:
    """Get the cached S3 client singleton."""
    global _s3_client
    if _s3_client is None:
        _s3_client = S3Client()
    return _s3_client
