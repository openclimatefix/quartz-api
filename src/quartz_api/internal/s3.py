"""S3 client."""
import os

import fsspec


class S3Client:
    """S3 client."""

    def __init__(self) -> None:
        """Initialise S3 client."""
        region = os.environ.get("AWS_DEFAULT_REGION")
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


_s3_client: S3Client | None = None


def get_s3_client() -> S3Client:
    """Get the S3 client."""
    global _s3_client
    if _s3_client is None:
        _s3_client = S3Client()
    return _s3_client
