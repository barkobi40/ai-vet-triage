"""
Creates the S3 media bucket if it doesn't already exist. Useful for local
dev against LocalStack (see docker-compose.yml); in production you'd
typically manage the bucket via IaC (Terraform/CDK) alongside lifecycle
rules and versioning.

Usage:
    python scripts/create_bucket.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.exceptions import ClientError

from app.core.config import get_settings


def create_bucket() -> None:
    settings = get_settings()
    client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
    )

    try:
        client.head_bucket(Bucket=settings.s3_bucket_name)
        print(f"Bucket '{settings.s3_bucket_name}' already exists. Skipping.")
        return
    except ClientError:
        pass

    if settings.aws_region == "us-east-1":
        client.create_bucket(Bucket=settings.s3_bucket_name)
    else:
        client.create_bucket(
            Bucket=settings.s3_bucket_name,
            CreateBucketConfiguration={"LocationConstraint": settings.aws_region},
        )
    print(f"Created bucket '{settings.s3_bucket_name}'.")


if __name__ == "__main__":
    create_bucket()
