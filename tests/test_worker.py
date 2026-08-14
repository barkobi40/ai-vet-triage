import json
from decimal import Decimal

import boto3
import pytest

from app.db import dynamodb, schema
from app.models.triage import Priority, TriageResult, TriageStatus

TRIAGE_ID = "11111111-1111-1111-1111-111111111111"
CREATED_AT = "2026-08-13T00:00:00+00:00"


async def _seed_uploaded_case(settings):
    item = {
        "PK": schema.triage_pk(TRIAGE_ID),
        "SK": schema.TRIAGE_SK,
        "GSI1PK": schema.priority_gsi1pk(Priority.PENDING),
        "GSI1SK": schema.created_at_gsi1sk(CREATED_AT),
        "triage_id": TRIAGE_ID,
        "status": TriageStatus.UPLOADED.value,
        "priority": Priority.PENDING.value,
        "pet_owner_description": "Limping on the back leg, whimpering when touched.",
        "species": "dog",
        "s3_bucket": settings.s3_bucket_name,
        "s3_key": f"uploads/{TRIAGE_ID}/original.mp4",
        "content_type": "video/mp4",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }
    await dynamodb.put_item(item)
    return item


def _fake_triage_result() -> TriageResult:
    return TriageResult(
        priority=Priority.YELLOW,
        confidence=0.82,
        summary="Dog is limping on a hind leg with signs of discomfort.",
        risk_factors=["favoring one hind leg", "vocalizing when leg touched"],
        next_steps=["Schedule an orthopedic exam within 24 hours"],
        species_detected="dog",
        requires_human_review=False,
        disclaimer=(
            "AI-generated triage suggestion. Must be confirmed by licensed "
            "veterinary staff before any clinical action."
        ),
    )


def _send_job_and_receive(settings, item):
    sqs = boto3.client("sqs", region_name=settings.aws_region)
    queue_url = sqs.get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(
            {"triage_id": TRIAGE_ID, "s3_bucket": settings.s3_bucket_name, "s3_key": item["s3_key"]}
        ),
    )
    response = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=1)
    return sqs, queue_url, response["Messages"][0]


@pytest.mark.asyncio
async def test_full_pipeline_flips_priority_and_acks_message(aws, monkeypatch):
    settings = aws
    item = await _seed_uploaded_case(settings)

    s3 = boto3.client("s3", region_name=settings.aws_region)
    s3.put_object(Bucket=settings.s3_bucket_name, Key=item["s3_key"], Body=b"fake video bytes")

    import worker.main as worker

    async def fake_run_triage(**kwargs) -> TriageResult:
        assert kwargs["description"] == item["pet_owner_description"]
        assert kwargs["media_path"].exists()
        return _fake_triage_result()

    # Stub the AI call: the worker's orchestration logic is what this test
    # covers, not Gemini itself — that needs real GEMINI_API_KEYS, so it
    # isn't part of the offline test suite (see tests/test_gemini_triage.py
    # for coverage of the Gemini call + key-rotation mechanics in isolation).
    monkeypatch.setattr(worker, "run_triage_assessment", fake_run_triage)

    sqs, queue_url, message = _send_job_and_receive(settings, item)
    await worker.handle_message(message)

    result_item = await dynamodb.get_item(schema.triage_pk(TRIAGE_ID), schema.TRIAGE_SK)
    assert result_item["status"] == TriageStatus.COMPLETE.value
    assert result_item["priority"] == Priority.YELLOW.value
    assert result_item["GSI1PK"] == "PRIORITY#YELLOW"
    assert result_item["GSI1SK"] == f"CREATED_AT#{CREATED_AT}"  # untouched by the worker
    assert result_item["triage_result"]["priority"] == "YELLOW"

    remaining = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=1)
    assert "Messages" not in remaining  # message was ack'd (deleted)


@pytest.mark.asyncio
async def test_redelivery_after_success_does_not_reprocess(aws, monkeypatch):
    settings = aws
    item = await _seed_uploaded_case(settings)
    await dynamodb.update_item(
        pk=schema.triage_pk(TRIAGE_ID),
        sk=schema.TRIAGE_SK,
        update_expression="SET #status = :complete",
        expression_attribute_names={"#status": "status"},
        expression_attribute_values={":complete": TriageStatus.COMPLETE.value},
    )

    import worker.main as worker

    def boom(*args, **kwargs):
        raise AssertionError("AI calls must not run again for an already-COMPLETE case")

    monkeypatch.setattr(worker, "run_triage_assessment", boom)

    should_ack = await worker.process_triage(TRIAGE_ID, settings.s3_bucket_name, item["s3_key"])
    assert should_ack is True


@pytest.mark.asyncio
async def test_ai_failure_marks_case_failed_and_leaves_message_for_retry(aws, monkeypatch):
    settings = aws
    item = await _seed_uploaded_case(settings)

    s3 = boto3.client("s3", region_name=settings.aws_region)
    s3.put_object(Bucket=settings.s3_bucket_name, Key=item["s3_key"], Body=b"fake video bytes")

    import worker.main as worker
    from app.services.ai.gemini_client import AllGeminiKeysExhaustedError

    async def failing_run_triage(**kwargs) -> TriageResult:
        # Simulates every configured Gemini API key hitting its quota —
        # see tests/test_gemini_triage.py for the rotation mechanics itself.
        raise AllGeminiKeysExhaustedError("all keys exhausted (simulated)")

    monkeypatch.setattr(worker, "run_triage_assessment", failing_run_triage)

    _sqs, _queue_url, message = _send_job_and_receive(settings, item)
    await worker.handle_message(message)  # must not raise; failure is caught internally

    result_item = await dynamodb.get_item(schema.triage_pk(TRIAGE_ID), schema.TRIAGE_SK)
    assert result_item["status"] == TriageStatus.FAILED.value
    # Priority/GSI1PK untouched: the case still surfaces on the dashboard, now flagged FAILED.
    assert result_item["GSI1PK"] == "PRIORITY#PENDING"


@pytest.mark.asyncio
async def test_case_context_converts_decimal_pet_age_to_json_safe_float(aws, monkeypatch):
    """DynamoDB hands pet_age back as a Decimal (app/routers/triage.py writes
    it that way to satisfy boto3's float restriction). publish_triage_update()
    -> json.dumps() can't serialize Decimal, so this proves _case_context()
    actually converts it back — a real bug, not a hypothetical one, since
    Decimal(3) would otherwise crash the publish call for every case that
    has an owner/pet profile attached."""
    settings = aws
    item = await _seed_uploaded_case(settings)
    await dynamodb.update_item(
        pk=schema.triage_pk(TRIAGE_ID),
        sk=schema.TRIAGE_SK,
        update_expression="SET owner_name = :o, pet_name = :p, pet_age = :a",
        expression_attribute_values={":o": "Jane Doe", ":p": "Rex", ":a": Decimal("3.5")},
    )

    s3 = boto3.client("s3", region_name=settings.aws_region)
    s3.put_object(Bucket=settings.s3_bucket_name, Key=item["s3_key"], Body=b"fake video bytes")

    import worker.main as worker

    published: list[dict] = []

    async def fake_publish(payload: dict) -> None:
        json.dumps(payload)  # must not raise TypeError on a Decimal
        published.append(payload)

    async def fake_run_triage(**kwargs) -> TriageResult:
        return _fake_triage_result()

    monkeypatch.setattr(worker, "run_triage_assessment", fake_run_triage)
    monkeypatch.setattr(worker, "publish_triage_update", fake_publish)

    sqs, queue_url, message = _send_job_and_receive(settings, item)
    await worker.handle_message(message)

    complete_payload = next(p for p in published if p["status"] == "COMPLETE")
    assert complete_payload["owner_name"] == "Jane Doe"
    assert complete_payload["pet_name"] == "Rex"
    assert complete_payload["pet_age"] == 3.5
    assert isinstance(complete_payload["pet_age"], float)
