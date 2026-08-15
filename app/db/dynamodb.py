import asyncio
from functools import lru_cache
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from app.core.config import get_settings
from app.db.schema import GSI1_NAME


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
    """
    table = get_table()
    await asyncio.to_thread(table.put_item, Item=item)


async def get_item(pk: str, sk: str) -> dict[str, Any] | None:
    table = get_table()
    response = await asyncio.to_thread(table.get_item, Key={"PK": pk, "SK": sk})
    return response.get("Item")


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
    table = get_table()
    response = await asyncio.to_thread(
        table.query,
        IndexName=GSI1_NAME,
        KeyConditionExpression=Key("GSI1PK").eq(gsi1pk),
        ScanIndexForward=scan_index_forward,
    )
    return response.get("Items", [])
