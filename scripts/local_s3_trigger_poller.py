"""
Local dev/demo stand-in for the real S3 -> Lambda trigger.

Polls the media bucket for new objects under uploads/ and invokes the
*actual* Lambda handler code (lambda/s3_upload_trigger/handler.py,
imported unmodified) against a synthesized S3 event. This exists because
LocalStack's Lambda emulation is comparatively fragile to wire into a
one-command docker-compose demo (docker-in-docker, executor config, etc.)
compared to its S3/DynamoDB/SQS emulation, which is stable. In real AWS
this role is played by the Lambda deployed via scripts/deploy_s3_trigger.sh
— same handler code either way, so this poller can't drift from what
actually runs in production.

Usage:
    python scripts/local_s3_trigger_poller.py
"""
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "lambda" / "s3_upload_trigger"))

import boto3

from app.core.config import get_settings

POLL_INTERVAL_SECONDS = 2


def main() -> None:
    settings = get_settings()

    # handler.py builds its own boto3 clients at import time from ambient
    # env vars — exactly as it would inside real Lambda — so we set the
    # ones it needs before importing it, rather than modifying the handler.
    os.environ.setdefault("DYNAMODB_TABLE_NAME", settings.dynamodb_table_name)
    sqs = boto3.client("sqs", region_name=settings.aws_region, endpoint_url=settings.aws_endpoint_url)
    queue_url = sqs.get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]
    os.environ.setdefault("SQS_QUEUE_URL", queue_url)

    import handler  # noqa: E402 — the real Lambda handler, imported unmodified

    s3 = boto3.client("s3", region_name=settings.aws_region, endpoint_url=settings.aws_endpoint_url)
    print(f"Polling s3://{settings.s3_bucket_name}/uploads/ every {POLL_INTERVAL_SECONDS}s ...")

    seen: set[str] = set()
    while True:
        response = s3.list_objects_v2(Bucket=settings.s3_bucket_name, Prefix="uploads/")
        for obj in response.get("Contents", []):
            key = obj["Key"]
            if key in seen:
                continue
            seen.add(key)
            print(f"New object detected: {key}")
            event = {
                "Records": [
                    {"s3": {"bucket": {"name": settings.s3_bucket_name}, "object": {"key": key}}}
                ]
            }
            handler.handler(event, None)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
