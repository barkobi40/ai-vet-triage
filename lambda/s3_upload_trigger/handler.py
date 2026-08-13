"""
AWS Lambda function triggered by `s3:ObjectCreated:*` events on the
vet-triage media bucket (uploads/ prefix).

Responsibilities:
  1. Parse the S3 event to find which triage case's media just landed.
  2. Flip the DynamoDB record PENDING -> UPLOADED. The update is
     conditional on the record currently being PENDING, which makes this
     handler idempotent: S3 delivers notifications at-least-once, so a
     duplicate event for the same object must not enqueue a duplicate
     processing job.
  3. Enqueue a lightweight job onto SQS for the AI worker to pick up. The
     message only carries identifiers (triage_id, bucket, key) — the
     worker re-reads the full record from DynamoDB, keeping DynamoDB the
     single source of truth instead of duplicating state into the queue.

Deliberately dependency-free (stdlib + boto3, both present in the default
Lambda Python runtime) so this ships without a deployment package or layer,
and evolves independently of the FastAPI service's dependencies.

Required environment variables (set at deploy time):
  DYNAMODB_TABLE_NAME
  SQS_QUEUE_URL
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")

TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
QUEUE_URL = os.environ["SQS_QUEUE_URL"]

# Matches the key format minted by the FastAPI upload-url endpoint:
# uploads/{triage_id}/original.{ext}
KEY_PATTERN = re.compile(r"^uploads/(?P<triage_id>[0-9a-f-]{36})/")


def handler(event, context):
    table = dynamodb.Table(TABLE_NAME)

    # A single invocation can batch multiple S3 events.
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        match = KEY_PATTERN.match(key)
        if not match:
            logger.warning("Ignoring S3 object with unrecognized key format: %s", key)
            continue

        _process_upload(table, triage_id=match.group("triage_id"), bucket=bucket, key=key)

    return {"statusCode": 200}


def _process_upload(table, triage_id: str, bucket: str, key: str) -> None:
    now = datetime.now(timezone.utc).isoformat()

    try:
        table.update_item(
            Key={"PK": f"TRIAGE#{triage_id}", "SK": "METADATA"},
            UpdateExpression="SET #status = :uploaded, updated_at = :now",
            ConditionExpression="#status = :pending",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":uploaded": "UPLOADED",
                ":pending": "PENDING",
                ":now": now,
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # Either the record isn't PENDING anymore (duplicate S3 event —
            # expected and safe to drop) or it doesn't exist at all (should
            # be impossible given the API always writes DynamoDB before
            # returning the presigned URL, but we don't want an Lambda retry
            # loop over a record that will never appear).
            logger.warning(
                "Skipping triage_id=%s: record not in PENDING state "
                "(duplicate event or missing record).",
                triage_id,
            )
            return
        raise  # unexpected DynamoDB error -> let Lambda's retry policy handle it

    sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=json.dumps({"triage_id": triage_id, "s3_bucket": bucket, "s3_key": key}),
        MessageAttributes={
            "triage_id": {"DataType": "String", "StringValue": triage_id},
        },
    )
    logger.info("Enqueued triage_id=%s for processing", triage_id)
