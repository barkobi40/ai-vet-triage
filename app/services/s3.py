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


def generate_presigned_download_url(s3_bucket: str, s3_key: str) -> str:
    """Presigned GET URL so the vet dashboard's <video>/<img> element can
    play back the uploaded media directly from S3, without the API server
    ever proxying the file bytes."""
    settings = get_settings()
    client = get_s3_client()
    return client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": s3_bucket, "Key": s3_key},
        ExpiresIn=settings.presigned_url_expiry_seconds,
    )
