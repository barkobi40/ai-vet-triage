"""
Creates the single-table DynamoDB design used by AI Vet-Triage.

In a production deployment this table would be defined via IaC
(Terraform/CDK) alongside the rest of the stack. This script exists to
spin the table up locally — e.g. against DynamoDB Local or LocalStack —
for development, and doubles as executable documentation of the schema.
See app/db/schema.py for the key-design rationale.

Usage:
    python scripts/create_table.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.db.schema import GSI1_NAME


def create_table() -> None:
    settings = get_settings()
    client = boto3.client(
        "dynamodb",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
    )

    table_name = settings.dynamodb_table_name
    if table_name in client.list_tables()["TableNames"]:
        print(f"Table '{table_name}' already exists. Skipping.")
        return

    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": GSI1_NAME,
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        # On-demand billing: triage upload volume is spiky and unpredictable,
        # so pay-per-request avoids provisioned-capacity guesswork.
        BillingMode="PAY_PER_REQUEST",
    )

    print(f"Creating table '{table_name}'...")
    client.get_waiter("table_exists").wait(TableName=table_name)
    print(f"Table '{table_name}' is active.")


if __name__ == "__main__":
    try:
        create_table()
    except ClientError as exc:
        print(f"Failed to create table: {exc}")
        raise
