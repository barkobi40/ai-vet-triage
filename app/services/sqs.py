import asyncio
from functools import lru_cache
from typing import Any

import boto3

from app.core.config import get_settings


@lru_cache
def get_sqs_client():
    settings = get_settings()
    return boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
    )


@lru_cache
def get_queue_url() -> str:
    settings = get_settings()
    return get_sqs_client().get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]


async def receive_messages(max_messages: int = 1, wait_time_seconds: int = 20) -> list[dict[str, Any]]:
    """
    Long-polls the processing queue. wait_time_seconds=20 (SQS's max) means
    the worker blocks on the receive call until a message arrives or the
    window elapses, instead of busy-polling — far fewer empty API calls
    than short polling under normal (non-spiky) load.
    """
    client = get_sqs_client()
    response = await asyncio.to_thread(
        client.receive_message,
        QueueUrl=get_queue_url(),
        MaxNumberOfMessages=max_messages,
        WaitTimeSeconds=wait_time_seconds,
        VisibilityTimeout=get_settings().sqs_visibility_timeout_seconds,
    )
    return response.get("Messages", [])


async def delete_message(receipt_handle: str) -> None:
    client = get_sqs_client()
    await asyncio.to_thread(
        client.delete_message, QueueUrl=get_queue_url(), ReceiptHandle=receipt_handle
    )
