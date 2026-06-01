"""S3 client
"""

import boto3
from botocore.client import Config


class S3Client:
    """S3 client
    """

    def __init__(self, region_name: str):
        self.client = boto3.client("s3", region_name=region_name, config=Config(signature_version="s3v4"))

    def get_presigned_url(self, bucket: str, key: str) -> str:
        """Get a pre-signed URL for a file.
        """
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600,
        )

    def object_exists(self, bucket: str, key: str) -> bool:
        """Check if an object exists in S3.
        """
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except self.client.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            else:
                raise

_s3_client: S3Client | None = None

def get_s3_client() -> S3Client:
    """Get the S3 client.
    """
    global _s3_client
    if _s3_client is None:
        _s3_client = S3Client(region_name="eu-west-1")
    return _s3_client
