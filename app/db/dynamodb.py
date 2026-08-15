import asyncio
import logging
from functools import lru_cache
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from app.core.config import get_settings
from app.db import local_store
from app.db.schema import GSI1_NAME, GSI2_NAME

logger = logging.getLogger(__name__)

# Raised locally (no network round-trip needed) when AWS can't be reached
# or authenticated to at all — as opposed to e.g. ConditionalCheckFailedException,
# which is a ClientError with real business meaning to callers of update_item()
# and must never be silently swallowed. put_item/get_item/query_gsi1 below
# never use ConditionExpression, so any ClientError from them is safely
# treated as "AWS unavailable," not a business-logic failure.
_AWS_UNAVAILABLE_EXCEPTIONS = (NoCredentialsError, EndpointConnectionError, ClientError)


@lru_cache
def get_dynamodb_resource():
    settings = get_settings()
    return boto3.resource(
        "dynamodb",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
    )


@lru_cache
def get_table():
    return get_dynamodb_resource().Table(get_settings().dynamodb_table_name)


async def put_item(item: dict[str, Any]) -> None:
    """
    boto3 is synchronous — every call is blocking network I/O. We offload
    it to the default thread pool via asyncio.to_thread so a DynamoDB
    round-trip never blocks the FastAPI event loop from serving other
    requests concurrently.

    Falls back to an in-memory store (app/db/local_store.py) if DynamoDB
    itself can't be reached — see that module's docstring for why this
    exists and its limits.
    """
    try:
        table = get_table()
        await asyncio.to_thread(table.put_item, Item=item)
    except _AWS_UNAVAILABLE_EXCEPTIONS as exc:
        logger.warning("DynamoDB unavailable (%s); falling back to local in-memory store.", exc)
        local_store.put_item(item)


async def get_item(pk: str, sk: str) -> dict[str, Any] | None:
    try:
        table = get_table()
        response = await asyncio.to_thread(table.get_item, Key={"PK": pk, "SK": sk})
        return response.get("Item")
    except _AWS_UNAVAILABLE_EXCEPTIONS as exc:
        logger.warning("DynamoDB unavailable (%s); falling back to local in-memory store.", exc)
        return local_store.get_item(pk, sk)


async def update_item(
    pk: str,
    sk: str,
    update_expression: str,
    expression_attribute_values: dict[str, Any],
    expression_attribute_names: dict[str, str] | None = None,
    condition_expression: str | None = None,
) -> dict[str, Any]:
    """
    Thin async wrapper around DynamoDB UpdateItem. If condition_expression
    is given and not satisfied, boto3 raises a ClientError with code
    ConditionalCheckFailedException — callers using this for optimistic
    claiming (see worker/main.py) must catch that explicitly.
    """
    table = get_table()
    kwargs: dict[str, Any] = {
        "Key": {"PK": pk, "SK": sk},
        "UpdateExpression": update_expression,
        "ExpressionAttributeValues": expression_attribute_values,
        "ReturnValues": "ALL_NEW",
    }
    if expression_attribute_names:
        kwargs["ExpressionAttributeNames"] = expression_attribute_names
    if condition_expression:
        kwargs["ConditionExpression"] = condition_expression

    response = await asyncio.to_thread(table.update_item, **kwargs)
    return response["Attributes"]


async def query_gsi1(gsi1pk: str, *, scan_index_forward: bool = True) -> list[dict[str, Any]]:
    """Query GSI1 for every item sharing a given GSI1PK (e.g. the vet
    directory's constant partition — see app/db/schema.py). Single-table
    design means this same helper serves any current or future entity
    type indexed on GSI1, not just one."""
    try:
        table = get_table()
        response = await asyncio.to_thread(
            table.query,
            IndexName=GSI1_NAME,
            KeyConditionExpression=Key("GSI1PK").eq(gsi1pk),
            ScanIndexForward=scan_index_forward,
        )
        return response.get("Items", [])
    except _AWS_UNAVAILABLE_EXCEPTIONS as exc:
        logger.warning("DynamoDB unavailable (%s); falling back to local in-memory store.", exc)
        return local_store.query_gsi1(gsi1pk, scan_index_forward=scan_index_forward)


async def get_by_gsi2(gsi2pk: str) -> dict[str, Any] | None:
    """Single-item lookup by GSI2 (account-by-email — see app/db/schema.py
    and app/routers/auth.py). Unlike query_gsi1, GSI2PK alone is unique
    per item, so this returns at most one account regardless of role."""
    try:
        table = get_table()
        response = await asyncio.to_thread(
            table.query,
            IndexName=GSI2_NAME,
            KeyConditionExpression=Key("GSI2PK").eq(gsi2pk),
            Limit=1,
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except _AWS_UNAVAILABLE_EXCEPTIONS as exc:
        logger.warning("DynamoDB unavailable (%s); falling back to local in-memory store.", exc)
        return local_store.get_by_gsi2(gsi2pk)
