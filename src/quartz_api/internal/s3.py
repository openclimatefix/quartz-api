"""S3 client."""
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
    source_bucket: str,
    icechunk_bucket: str,
    icechunk_prefix: str,
) -> None:
    """Set satellite S3 config from the parsed application config.

    Called once at startup from ``_create_server`` before routers are registered.
    """
    global _config, _s3_client
    _config = {
        "region": region,
        "geotiff_bucket": geotiff_bucket,
        "source_bucket": source_bucket,
        "icechunk_bucket": icechunk_bucket,
        "icechunk_prefix": icechunk_prefix,
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


def get_source_bucket() -> str:
    """Bucket holding the raw satellite source data for ingest."""
    return _require_config()["source_bucket"]


def get_icechunk_bucket() -> str:
    """Bucket holding the Icechunk store."""
    return _require_config()["icechunk_bucket"]


def get_icechunk_prefix() -> str:
    """Key prefix for the Icechunk store."""
    return _require_config()["icechunk_prefix"]


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

    def get_presigned_url(self, bucket: str, key: str) -> str:
        """Get a pre-signed URL for a file."""
        return self.fs.sign(f"s3://{bucket}/{key}", expiration=3600)

    def object_exists(self, bucket: str, key: str) -> bool:
        """Check if an object exists in S3."""
        return self.fs.exists(f"s3://{bucket}/{key}")

    def upload_bytes(self, bucket: str, key: str, data: bytes) -> None:
        """Upload raw bytes to an S3 key."""
        with self.fs.open(f"s3://{bucket}/{key}", "wb") as f:
            f.write(data)


def get_s3_client() -> S3Client:
    """Get the cached S3 client singleton."""
    global _s3_client
    if _s3_client is None:
        _s3_client = S3Client()
    return _s3_client
