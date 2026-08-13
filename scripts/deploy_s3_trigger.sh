#!/usr/bin/env bash
# Deploys the S3-upload-trigger Lambda and wires it to the media bucket and
# processing queue. This is a thin AWS-CLI script for demo/dev use — in a
# real production stack this would be Terraform/CDK/SAM, but this keeps the
# whole project runnable with just an AWS account and the CLI.
#
# Prerequisites: `python scripts/create_table.py` and
# `python scripts/create_queue.py` have already been run, and the S3 bucket
# (S3_BUCKET_NAME) already exists.
#
# Usage:
#   ./scripts/deploy_s3_trigger.sh
set -euo pipefail

FUNCTION_NAME="${FUNCTION_NAME:-vet-triage-s3-upload-trigger}"
ROLE_NAME="${ROLE_NAME:-vet-triage-s3-trigger-role}"
TABLE_NAME="${DYNAMODB_TABLE_NAME:-vet-triage}"
QUEUE_NAME="${SQS_QUEUE_NAME:-vet-triage-processing}"
BUCKET_NAME="${S3_BUCKET_NAME:-ai-vet-triage-media-dev}"
REGION="${AWS_REGION:-us-east-1}"

LAMBDA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lambda/s3_upload_trigger" && pwd)"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
QUEUE_URL=$(aws sqs get-queue-url --queue-name "$QUEUE_NAME" --region "$REGION" --query QueueUrl --output text)

# --- IAM role (idempotent) ---
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://${LAMBDA_DIR}/trust_policy.json" >/dev/null

  sed -e "s/__REGION__/${REGION}/g" \
      -e "s/__ACCOUNT_ID__/${ACCOUNT_ID}/g" \
      -e "s/__TABLE_NAME__/${TABLE_NAME}/g" \
      -e "s/__QUEUE_NAME__/${QUEUE_NAME}/g" \
      "${LAMBDA_DIR}/execution_policy.json.tmpl" > /tmp/execution_policy.json

  aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name vet-triage-s3-trigger-inline \
    --policy-document file:///tmp/execution_policy.json

  echo "Waiting for IAM role propagation..."
  sleep 10
fi
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# --- Package & deploy function code ---
rm -f /tmp/s3_upload_trigger.zip
(cd "$LAMBDA_DIR" && zip -q -r /tmp/s3_upload_trigger.zip handler.py)

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file fileb:///tmp/s3_upload_trigger.zip \
    --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
else
  aws lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler handler.handler \
    --timeout 30 \
    --memory-size 128 \
    --zip-file fileb:///tmp/s3_upload_trigger.zip \
    --region "$REGION" >/dev/null
  aws lambda wait function-active --function-name "$FUNCTION_NAME" --region "$REGION"
fi

aws lambda update-function-configuration \
  --function-name "$FUNCTION_NAME" \
  --environment "Variables={DYNAMODB_TABLE_NAME=${TABLE_NAME},SQS_QUEUE_URL=${QUEUE_URL}}" \
  --region "$REGION" >/dev/null
aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"

LAMBDA_ARN=$(aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" \
  --query Configuration.FunctionArn --output text)

# --- Allow S3 to invoke this function ---
aws lambda add-permission \
  --function-name "$FUNCTION_NAME" \
  --statement-id s3invoke \
  --action lambda:InvokeFunction \
  --principal s3.amazonaws.com \
  --source-arn "arn:aws:s3:::${BUCKET_NAME}" \
  --source-account "$ACCOUNT_ID" \
  --region "$REGION" >/dev/null 2>&1 || echo "Permission 's3invoke' already exists, skipping."

# --- Wire the S3 -> Lambda notification, scoped to uploads/ ---
cat > /tmp/notification.json <<EOF
{
  "LambdaFunctionConfigurations": [
    {
      "LambdaFunctionArn": "${LAMBDA_ARN}",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "uploads/"}]}}
    }
  ]
}
EOF
aws s3api put-bucket-notification-configuration \
  --bucket "$BUCKET_NAME" \
  --notification-configuration file:///tmp/notification.json \
  --region "$REGION"

echo "Deployed. Lambda ARN: ${LAMBDA_ARN}"
