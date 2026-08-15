"""
Standalone AI triage worker.

Long-polls the SQS processing queue, downloads the referenced media from
S3, and sends it straight to Gemini 1.5 Flash for a multimodal triage
assessment (Structured Output, validated against TriageResult) — Gemini
processes video frames and the audio track natively in one call, so there's
no separate transcription or frame-extraction step. Writes the result back
to DynamoDB — flipping GSI1PK to the real priority so the clinic
dashboard's "all RED, newest first" query picks it up immediately.

This is a separate deployable process from the FastAPI API (its own
container/ECS service in production), but shares the app/ package's
config, DB, and service modules since both run from the same codebase.

Run:
    python -m worker.main

See README section "Running and testing the worker locally" for the full
local dev loop (LocalStack/moto, no real AWS or Gemini key required for
the test suite; real GEMINI_API_KEYS are required to run this against
live media).
"""
import asyncio
import json
import logging
import signal
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.db import dynamodb, schema
from app.models.triage import Priority, TriageResult, TriageStatus
from app.services import sqs
from app.services.ai.triage_llm import run_triage_assessment
from app.services.media import download_media
from app.services.pubsub import publish_triage_update

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")

_shutdown = asyncio.Event()


def _request_shutdown(signum, _frame) -> None:
    logger.info("Received signal %s; shutting down after the in-flight message completes...", signum)
    _shutdown.set()


async def run_forever() -> None:
    logger.info("Worker started. Long-polling SQS queue '%s'...", get_settings().sqs_queue_name)
    while not _shutdown.is_set():
        messages = await sqs.receive_messages(max_messages=1, wait_time_seconds=20)
        for message in messages:
            if _shutdown.is_set():
                break
            await handle_message(message)


async def handle_message(message: dict[str, Any]) -> None:
    receipt_handle = message["ReceiptHandle"]

    try:
        payload = json.loads(message["Body"])
        triage_id: str = payload["triage_id"]
        s3_bucket: str = payload["s3_bucket"]
        s3_key: str = payload["s3_key"]
    except (KeyError, json.JSONDecodeError):
        logger.error("Malformed SQS message body, dropping without retry: %r", message.get("Body"))
        await sqs.delete_message(receipt_handle)
        return

    logger.info("Received job for triage_id=%s", triage_id)

    try:
        should_ack = await process_triage(triage_id, s3_bucket, s3_key)
    except Exception:
        logger.exception("Unhandled error processing triage_id=%s", triage_id)
        await _mark_failed(triage_id)
        # Do NOT delete the message: leave it for SQS to redeliver once the
        # visibility timeout expires. After sqs_max_receive_count attempts
        # it's routed to the DLQ automatically.
        return

    if should_ack:
        await sqs.delete_message(receipt_handle)
        logger.info("triage_id=%s done, message acked", triage_id)


async def process_triage(triage_id: str, s3_bucket: str, s3_key: str) -> bool:
    """Runs the full pipeline for one case. Returns True if the SQS message
    should be deleted (ack'd), False if it should be left for another
    worker/attempt."""
    pk = schema.triage_pk(triage_id)
    item = await dynamodb.get_item(pk, schema.TRIAGE_SK)
    if item is None:
        logger.error("triage_id=%s not found in DynamoDB; dropping message", triage_id)
        return True  # nothing to retry towards

    if item["status"] == TriageStatus.COMPLETE.value:
        # Redelivered after a successful run whose ack didn't make it back to
        # SQS in time (e.g. the worker crashed between the DynamoDB write and
        # DeleteMessage). The result already exists — ack and skip re-running
        # (and re-billing) the AI calls.
        logger.info("triage_id=%s already COMPLETE; ack without reprocessing", triage_id)
        return True

    if not await _claim_for_processing(pk):
        logger.info("triage_id=%s already claimed by another worker; skipping", triage_id)
        return False

    await publish_triage_update(
        {
            "triage_id": triage_id,
            "status": TriageStatus.PROCESSING.value,
            "priority": item["priority"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **_case_context(item),
        }
    )

    with tempfile.TemporaryDirectory(prefix=f"triage-{triage_id}-") as tmp_dir:
        media_path = await download_media(s3_bucket, s3_key, Path(tmp_dir))
        result = await run_triage_assessment(
            description=item["pet_owner_description"],
            species=item.get("species"),
            media_path=media_path,
        )

    await _save_result(pk, triage_id, item, result)
    return True


def _case_context(item: dict[str, Any]) -> dict[str, Any]:
    """Owner/pet/media fields the vet dashboard's case review panel needs,
    pulled from the DynamoDB item rather than re-derived — DynamoDB is the
    single source of truth for this data (see app/routers/triage.py, which
    is what originally wrote it from the owner's local profile)."""
    pet_age = item.get("pet_age")
    return {
        "owner_name": item.get("owner_name"),
        "pet_name": item.get("pet_name"),
        # DynamoDB hands back a Decimal (it was written as one — see
        # app/routers/triage.py — to satisfy boto3's float restriction);
        # json.dumps() can't serialize Decimal, so convert back here.
        "pet_age": float(pet_age) if pet_age is not None else None,
        "pet_owner_description": item.get("pet_owner_description"),
        "species": item.get("species"),
        "vet_id": item.get("vet_id"),
        "clinic_name": item.get("clinic_name"),
        "s3_bucket": item.get("s3_bucket"),
        "s3_key": item.get("s3_key"),
    }


async def _claim_for_processing(pk: str) -> bool:
    """Optimistically claims the case for this worker by moving
    UPLOADED|FAILED -> PROCESSING. Guards against two workers processing
    the same message concurrently (e.g. a visibility-timeout race)."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        await dynamodb.update_item(
            pk=pk,
            sk=schema.TRIAGE_SK,
            update_expression="SET #status = :processing, updated_at = :now",
            condition_expression="#status = :uploaded OR #status = :failed",
            expression_attribute_names={"#status": "status"},
            expression_attribute_values={
                ":processing": TriageStatus.PROCESSING.value,
                ":uploaded": TriageStatus.UPLOADED.value,
                ":failed": TriageStatus.FAILED.value,
                ":now": now,
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _dynamo_safe_result(result: TriageResult) -> dict[str, Any]:
    # DynamoDB's boto3 resource rejects native Python float (it demands
    # Decimal for numeric attributes) — round-trip through JSON to convert
    # TriageResult.confidence without hand-walking the dict.
    return json.loads(result.model_dump_json(), parse_float=Decimal)


async def _save_result(pk: str, triage_id: str, item: dict[str, Any], result: TriageResult) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await dynamodb.update_item(
        pk=pk,
        sk=schema.TRIAGE_SK,
        # GSI1SK is intentionally left untouched: it holds the original
        # submission timestamp, so the dashboard's per-priority queue stays
        # ordered by "when the case came in," not "when triage finished."
        update_expression=(
            "SET #status = :complete, #priority = :priority, GSI1PK = :gsi1pk, "
            "triage_result = :result, updated_at = :now"
        ),
        expression_attribute_names={
            "#status": "status",
            "#priority": "priority",
        },
        expression_attribute_values={
            ":complete": TriageStatus.COMPLETE.value,
            ":priority": result.priority.value,
            ":gsi1pk": schema.priority_gsi1pk(result.priority.value),
            ":result": _dynamo_safe_result(result),
            ":now": now,
        },
    )
    logger.info(
        "triage_id=%s classified as %s (confidence=%.2f)", triage_id, result.priority.value, result.confidence
    )
    await publish_triage_update(
        {
            "triage_id": triage_id,
            "status": TriageStatus.COMPLETE.value,
            "priority": result.priority.value,
            "summary": result.summary,
            "confidence": result.confidence,
            "requires_human_review": result.requires_human_review,
            "risk_factors": result.risk_factors,
            "next_steps": result.next_steps,
            "updated_at": now,
            **_case_context(item),
        }
    )


async def _mark_failed(triage_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        await dynamodb.update_item(
            pk=schema.triage_pk(triage_id),
            sk=schema.TRIAGE_SK,
            # Priority/GSI1PK are left as PRIORITY#PENDING (set at intake) so
            # the case still surfaces on the dashboard, now flagged FAILED —
            # visibly needs attention rather than silently vanishing.
            update_expression="SET #status = :failed, updated_at = :now",
            expression_attribute_names={"#status": "status"},
            expression_attribute_values={":failed": TriageStatus.FAILED.value, ":now": now},
        )
    except ClientError:
        logger.exception("Failed to mark triage_id=%s as FAILED in DynamoDB", triage_id)
        return

    await publish_triage_update(
        {
            "triage_id": triage_id,
            "status": TriageStatus.FAILED.value,
            "priority": Priority.PENDING.value,
            "updated_at": now,
        }
    )


def main() -> None:
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
