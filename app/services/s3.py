from functools import lru_cache

import boto3

from app.core.config import get_settings


@lru_cache
def get_s3_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
    )


def generate_presigned_upload_url(s3_key: str, content_type: str) -> str:
    """
    Presigned URL generation is a local signing operation (no network call
    to AWS), so it's safe to run synchronously inside an async endpoint
    without offloading it to a thread.
    """
    settings = get_settings()
    client = get_s3_client()
    return client.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.s3_bucket_name,
            "Key": s3_key,
            "ContentType": content_type,
        },
        ExpiresIn=settings.presigned_url_expiry_seconds,
    )
