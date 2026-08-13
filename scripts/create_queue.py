"""
Creates the SQS processing queue and its dead-letter queue (DLQ) that
decouple the S3-upload-trigger Lambda from the AI worker pool.

As with scripts/create_table.py, this is for local/dev provisioning (e.g.
LocalStack) and doubles as executable documentation of the queue config;
in production this would be defined via Terraform/CDK.

Usage:
    python scripts/create_queue.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.exceptions import ClientError

from app.core.config import get_settings


def _get_or_create_queue(client, name: str, attributes: dict) -> str:
    try:
        return client.get_queue_url(QueueName=name)["QueueUrl"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "AWS.SimpleQueueService.NonExistentQueue":
            raise

    response = client.create_queue(QueueName=name, Attributes=attributes)
    print(f"Created queue '{name}'.")
    return response["QueueUrl"]


def create_queues() -> None:
    settings = get_settings()
    client = boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
    )

    # DLQ first — the main queue's redrive policy needs its ARN.
    dlq_url = _get_or_create_queue(
        client,
        settings.sqs_dlq_name,
        attributes={
            # 14 days: the max window to notice and investigate failed cases.
            "MessageRetentionPeriod": str(14 * 24 * 60 * 60),
        },
    )
    dlq_arn = client.get_queue_attributes(
        QueueUrl=dlq_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]

    main_queue_url = _get_or_create_queue(
        client,
        settings.sqs_queue_name,
        attributes={
            "VisibilityTimeout": str(settings.sqs_visibility_timeout_seconds),
            "MessageRetentionPeriod": str(4 * 24 * 60 * 60),  # 4 days
            "RedrivePolicy": json.dumps(
                {
                    "deadLetterTargetArn": dlq_arn,
                    "maxReceiveCount": settings.sqs_max_receive_count,
                }
            ),
        },
    )

    print(f"Main queue: {main_queue_url}")
    print(f"DLQ:        {dlq_url}")


if __name__ == "__main__":
    create_queues()
