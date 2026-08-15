import asyncio
import logging
import random
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from botocore.exceptions import ClientError, NoCredentialsError
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from app.core.config import get_settings
from app.db import dynamodb, schema
from app.models.triage import (
    ALLOWED_CONTENT_TYPES,
    DISCLAIMER_TEXT,
    CaseListResponse,
    CaseSummary,
    Priority,
    TriageStatus,
    UploadUrlRequest,
    UploadUrlResponse,
    VetResponseRequest,
    VetResponseResult,
    VideoUrlResponse,
)
from app.services import local_storage
from app.services.medical_record_pdf import build_medical_record_pdf
from app.services.pubsub import publish_triage_update
from app.services.s3 import generate_presigned_download_url, generate_presigned_upload_url
from app.ws.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/triage", tags=["triage"])

# Raised locally (no network round-trip) when boto3 can't sign a request at
# all — no credentials configured, or a malformed endpoint. This is exactly
# the case a laptop with no AWS setup hits every time, so it's the trigger
# for the local-disk fallback rather than a fatal error.
_S3_UNAVAILABLE_EXCEPTIONS = (NoCredentialsError, ClientError)

# How long to wait before the mock local-mode triage "completes" — see
# _simulate_local_triage below. Long enough to look like the real
# "AI Analyzing..." step the frontend already shows, short enough that a
# demo isn't left staring at PENDING.
_MOCK_TRIAGE_DELAY_SECONDS = 2.5

# Professional-sounding, priority-specific stand-ins for the mock triage
# result (see _simulate_local_triage) — deliberately free of any "mock" /
# "local-mode" / "no AWS" wording, since this text is user-facing on both
# dashboards. The real disclaimer (DISCLAIMER_TEXT, shown on every case
# regardless of mock vs. real) is what actually discloses this is an
# AI-generated suggestion requiring human confirmation.
_MOCK_RISK_FACTOR_BY_PRIORITY = {
    Priority.RED: "Reported symptoms may indicate a time-sensitive condition requiring prompt evaluation.",
    Priority.YELLOW: "Reported symptoms warrant timely veterinary attention.",
    Priority.GREEN: "Reported symptoms appear non-urgent but should be monitored for any change.",
}
_MOCK_NEXT_STEP_BY_PRIORITY = {
    Priority.RED: "Seek emergency veterinary care as soon as possible.",
    Priority.YELLOW: "Schedule a veterinary visit within the next 24 hours.",
    Priority.GREEN: "Monitor at home and consult a veterinarian if symptoms persist or worsen.",
}

# Strong references to in-flight background tasks (mock triage, see
# _simulate_local_triage) — asyncio only holds a weak reference to a task
# once its handle goes out of scope, which can let it get garbage-collected
# mid-run. Discarded via the done-callback once each task finishes.
_background_tasks: set[asyncio.Task] = set()


def _run_in_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.post(
    "/upload-url",
    response_model=UploadUrlResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_upload_url(payload: UploadUrlRequest) -> UploadUrlResponse:
    """
    Step 1 of the ingestion flow:
      1. Mint a triage_id and an S3 object key scoped to it.
      2. Generate a presigned PUT URL so the client uploads media directly
         to S3 — the API server never touches the file bytes. If S3 itself
         is unreachable (no AWS credentials configured — the default state
         for this project running outside AWS/LocalStack), fall back to a
         PUT target on this same API server that writes the file to local
         disk instead (see PUT /{triage_id}/upload-local below and
         app/services/local_storage.py). The client-side fetch() is
         identical either way — it just PUTs bytes to whatever URL it's
         given.
      3. Persist a PENDING record (falls back to an in-memory store if
         DynamoDB is also unreachable — see app/db/dynamodb.py) so the
         case exists in the system before the upload even completes, and
         broadcast it immediately so it renders live on any connected
         dashboard without waiting on the AI worker (which needs SQS +
         Gemini regardless, so it can't be part of this fallback).
    """
    settings = get_settings()
    triage_id = str(uuid4())
    file_extension = ALLOWED_CONTENT_TYPES[payload.content_type]
    s3_key = f"uploads/{triage_id}/original.{file_extension}"

    try:
        upload_url = generate_presigned_upload_url(s3_key=s3_key, content_type=payload.content_type)
        storage_backend = "s3"
        s3_bucket = settings.s3_bucket_name
    except _S3_UNAVAILABLE_EXCEPTIONS as exc:
        logger.warning("S3 unavailable (%s); falling back to local disk storage for triage_id=%s", exc, triage_id)
        upload_url = f"{settings.api_v1_prefix}/triage/{triage_id}/upload-local"
        storage_backend = "local"
        s3_bucket = "local-storage"

    now = datetime.now(timezone.utc).isoformat()

    item = {
        "PK": schema.triage_pk(triage_id),
        "SK": schema.TRIAGE_SK,
        "GSI1PK": schema.priority_gsi1pk(Priority.PENDING),
        "GSI1SK": schema.created_at_gsi1sk(now),
        "triage_id": triage_id,
        "status": TriageStatus.PENDING.value,
        "priority": Priority.PENDING.value,
        "pet_owner_description": payload.pet_owner_description,
        "species": payload.species,
        "owner_name": payload.owner_name,
        "pet_name": payload.pet_name,
        # DynamoDB's boto3 resource rejects native Python float (needs
        # Decimal) — the same issue already hit once in worker/main.py's
        # TriageResult.confidence write; converting via str() avoids binary
        # float-precision artifacts in the resulting Decimal.
        "pet_age": Decimal(str(payload.pet_age)) if payload.pet_age is not None else None,
        "vet_id": payload.vet_id,
        "clinic_name": payload.clinic_name,
        "owner_id": payload.owner_id,
        "s3_bucket": s3_bucket,
        "s3_key": s3_key,
        "storage_backend": storage_backend,
        "content_type": payload.content_type,
        "created_at": now,
        "updated_at": now,
    }

    await dynamodb.put_item(item)

    broadcast_payload = {
        "triage_id": triage_id,
        "status": TriageStatus.PENDING.value,
        "priority": Priority.PENDING.value,
        "summary": payload.pet_owner_description,
        "owner_name": payload.owner_name,
        "pet_name": payload.pet_name,
        "species": payload.species,
        "vet_id": payload.vet_id,
        "clinic_name": payload.clinic_name,
        "updated_at": now,
    }
    # Two delivery paths, not redundant: manager.broadcast() reaches clients
    # connected to *this* process directly (what makes this visible on the
    # vet dashboard immediately in the common case of Redis not running
    # locally — see app/routers/ws.py's /ws/broadcast for the same pattern).
    # publish_triage_update() additionally fans this out over Redis for any
    # *other* API replica's connected clients in a real deployment. A
    # client connected to this process only ever receives one copy of a
    # given update either way; the vet dashboard's upsertCase() is also
    # keyed by triage_id and idempotent, so even a redundant delivery
    # wouldn't produce a duplicate row.
    await manager.broadcast(broadcast_payload)
    await publish_triage_update(broadcast_payload)

    if storage_backend == "local":
        # No real worker will ever pick this case up: the production
        # pipeline is S3 ObjectCreated -> Lambda -> SQS -> worker/main.py,
        # and none of that exists without real AWS credentials (which is
        # exactly the condition that put us on the local-storage fallback
        # above). Left alone, the case would sit at PENDING forever. This
        # schedules a short-delay mock completion so local testing/demoing
        # still sees the PENDING -> COMPLETE transition rather than a dead
        # end — see _simulate_local_triage.
        _run_in_background(_simulate_local_triage(triage_id, item))

    return UploadUrlResponse(
        triage_id=triage_id,
        upload_url=upload_url,
        s3_key=s3_key,
        s3_bucket=s3_bucket,
        expires_in=settings.presigned_url_expiry_seconds,
        status=TriageStatus.PENDING,
    )


async def _simulate_local_triage(triage_id: str, item: dict[str, Any]) -> None:
    """
    Local-mode stand-in for the real worker/main.py pipeline (Gemini
    multimodal triage) — see the call site in create_upload_url for why
    this only ever runs when storage_backend == "local". Flips the case
    PENDING -> COMPLETE with a randomized mock priority after a short
    delay, persists it, and broadcasts it exactly like a real worker
    would, so local testing/demoing sees a real transition instead of a
    case stuck at PENDING forever.
    """
    try:
        await asyncio.sleep(_MOCK_TRIAGE_DELAY_SECONDS)

        priority = random.choice([Priority.RED, Priority.YELLOW, Priority.GREEN])
        now = datetime.now(timezone.utc).isoformat()
        # Decimal, not float — DynamoDB's boto3 resource rejects native
        # float attributes (see the pet_age conversion above and
        # worker/main.py's _dynamo_safe_result for the same constraint).
        mock_confidence = Decimal("0.72")
        description = (item.get("pet_owner_description") or "").strip().rstrip(".")
        symptom_text = description[:160] if description else "symptoms reported by the pet owner"
        mock_result = {
            "priority": priority.value,
            "confidence": mock_confidence,
            "summary": (
                f"Initial AI Assessment: {symptom_text}. Evaluated for urgency and flagged for veterinary review."
            ),
            "risk_factors": [_MOCK_RISK_FACTOR_BY_PRIORITY[priority]],
            "next_steps": [_MOCK_NEXT_STEP_BY_PRIORITY[priority]],
            "species_detected": item.get("species") or "Unknown",
            "requires_human_review": True,
            "disclaimer": DISCLAIMER_TEXT,
        }

        await dynamodb.put_item(
            {
                **item,
                "status": TriageStatus.COMPLETE.value,
                "priority": priority.value,
                "GSI1PK": schema.priority_gsi1pk(priority.value),
                "triage_result": mock_result,
                "updated_at": now,
            }
        )
        logger.info("triage_id=%s mock-completed with priority=%s (local mode)", triage_id, priority.value)

        broadcast_payload = {
            "triage_id": triage_id,
            "status": TriageStatus.COMPLETE.value,
            "priority": priority.value,
            "summary": mock_result["summary"],
            "confidence": float(mock_confidence),
            "requires_human_review": mock_result["requires_human_review"],
            "risk_factors": mock_result["risk_factors"],
            "next_steps": mock_result["next_steps"],
            "updated_at": now,
            "owner_name": item.get("owner_name"),
            "pet_name": item.get("pet_name"),
            "species": item.get("species"),
            "vet_id": item.get("vet_id"),
            "clinic_name": item.get("clinic_name"),
        }
        await manager.broadcast(broadcast_payload)
        await publish_triage_update(broadcast_payload)
    except Exception:
        # Fire-and-forget background task — nothing else observes/awaits
        # it, so an uncaught exception here would otherwise only surface
        # as an easy-to-miss "Task exception was never retrieved" log line.
        logger.exception("Mock local triage failed for triage_id=%s", triage_id)


def _item_to_case_summary(item: dict[str, Any]) -> CaseSummary:
    """Converts a raw DynamoDB (or local_store) item into the same shape a
    WebSocket push uses, so GET /triage results can feed straight into the
    dashboards' existing addRow()/upsertCase() rendering path."""
    triage_result = item.get("triage_result") or {}
    pet_age = item.get("pet_age")
    confidence = triage_result.get("confidence")
    return CaseSummary(
        triage_id=item["triage_id"],
        status=item["status"],
        priority=item["priority"],
        summary=triage_result.get("summary") or item.get("pet_owner_description"),
        pet_owner_description=item.get("pet_owner_description"),
        owner_name=item.get("owner_name"),
        pet_name=item.get("pet_name"),
        species=item.get("species"),
        pet_age=float(pet_age) if pet_age is not None else None,
        vet_id=item.get("vet_id"),
        clinic_name=item.get("clinic_name"),
        confidence=float(confidence) if confidence is not None else None,
        risk_factors=triage_result.get("risk_factors"),
        next_steps=triage_result.get("next_steps"),
        requires_human_review=triage_result.get("requires_human_review"),
        vet_response=item.get("vet_response"),
        updated_at=item.get("updated_at"),
    )


@router.patch("/{triage_id}/vet-response", response_model=VetResponseResult)
async def submit_vet_response(triage_id: str, payload: VetResponseRequest) -> VetResponseResult:
    """
    Persists the vet dashboard's "Send Update to Pet Owner" action — this
    used to only broadcast over WebSocket (see git history), which meant a
    vet's response never survived a page refresh on either dashboard.
    Fetches the current item, applies the vet's status/response on top of
    it, and writes the whole thing back — same read-modify-write pattern
    as _simulate_local_triage, and works under the local in-memory
    fallback the same way (see app/db/dynamodb.py).
    """
    item = await dynamodb.get_item(schema.triage_pk(triage_id), schema.TRIAGE_SK)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    now = datetime.now(timezone.utc).isoformat()
    updated_item = {
        **item,
        "status": payload.status,
        "vet_response": payload.vet_response,
        "updated_at": now,
    }
    await dynamodb.put_item(updated_item)

    case_summary = _item_to_case_summary(updated_item)
    broadcast_payload = case_summary.model_dump()
    await manager.broadcast(broadcast_payload)
    await publish_triage_update(broadcast_payload)

    return VetResponseResult(case=case_summary, recipients=manager.connection_count)


@router.get("", response_model=CaseListResponse)
async def list_cases(owner_id: str | None = None, vet_id: str | None = None) -> CaseListResponse:
    """
    Populates the dashboards on page load/refresh, so a refresh doesn't
    wipe the table back down to only-future-WebSocket-pushes. No dedicated
    "list everything" index exists (see app/db/schema.py) — GSI1 is
    partitioned by priority for the vet queue's real access pattern, so
    this merges all 4 priority partitions (a handful of queries at this
    project's demo scale) and filters by owner_id/vet_id in Python if
    given. owner_id scopes the pet-owner dashboard to "this browser's
    cases"; vet_id strictly scopes the vet dashboard to cases assigned to
    that vet/clinic (see web/vet_dashboard.html's loadExistingCases) — a
    case with no vet_id assigned isn't visible to any vet queue, matching
    "only cases explicitly assigned to their clinic." Neither filter is a
    real security boundary (there's no authentication in this demo, so a
    client could request any owner_id/vet_id) — it's data-scoping, not
    access control.
    """
    items: list[dict[str, Any]] = []
    for priority in (Priority.PENDING, Priority.RED, Priority.YELLOW, Priority.GREEN):
        items.extend(await dynamodb.query_gsi1(schema.priority_gsi1pk(priority.value), scan_index_forward=False))

    if owner_id:
        items = [i for i in items if i.get("owner_id") == owner_id]
    if vet_id:
        items = [i for i in items if i.get("vet_id") == vet_id]

    return CaseListResponse(cases=[_item_to_case_summary(i) for i in items])


@router.get("/medical-record")
async def download_medical_record(owner_id: str) -> Response:
    """
    The pet owner dashboard's "Medical Record" button: every case on file
    for this owner_id, as a downloadable PDF (symptoms, AI assessment,
    vet responses) — see app/services/medical_record_pdf.py. Reuses the
    same priority-partition merge as list_cases, since there's still no
    dedicated "list everything" index (see app/db/schema.py).
    """
    items: list[dict[str, Any]] = []
    for priority in (Priority.PENDING, Priority.RED, Priority.YELLOW, Priority.GREEN):
        items.extend(await dynamodb.query_gsi1(schema.priority_gsi1pk(priority.value), scan_index_forward=False))
    items = [i for i in items if i.get("owner_id") == owner_id]
    # Chronological (oldest first) reads better as a printed record than
    # the dashboards' newest-first live queue ordering.
    items.sort(key=lambda i: i.get("created_at") or i.get("updated_at") or "")

    cases = [_item_to_case_summary(i) for i in items]
    owner_name = items[0].get("owner_name") if items else None
    pet_name = items[0].get("pet_name") if items else None
    pdf_bytes = build_medical_record_pdf(cases, owner_name=owner_name, pet_name=pet_name)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="medical-record-{owner_id}.pdf"'},
    )


@router.put("/{triage_id}/upload-local", status_code=status.HTTP_204_NO_CONTENT)
async def upload_local_file(triage_id: str, request: Request) -> None:
    """
    Local-disk fallback target for the presigned-PUT-URL flow above — only
    ever reached when S3 wasn't available at case-creation time, in which
    case create_upload_url() pointed the client's PUT here instead of at a
    real S3 presigned URL. Writes the raw request body to
    static/uploads/{triage_id}/original.{ext} (see
    app/services/local_storage.py), the same path implied by the case's
    own s3_key, so GET /{triage_id}/video-url can serve it back.
    """
    item = await dynamodb.get_item(schema.triage_pk(triage_id), schema.TRIAGE_SK)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    body = await request.body()
    local_storage.save_file(item["s3_key"], body)


@router.get("/{triage_id}/video-url", response_model=VideoUrlResponse)
async def get_video_url(triage_id: str) -> VideoUrlResponse:
    """Used by the vet dashboard's embedded player: looks up the case's S3
    location and returns a short-lived presigned GET URL, rather than
    proxying the media through the API server."""
    settings = get_settings()
    item = await dynamodb.get_item(schema.triage_pk(triage_id), schema.TRIAGE_SK)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    if item.get("storage_backend") == "local":
        video_url = local_storage.local_url_for(item["s3_key"])
    else:
        video_url = generate_presigned_download_url(s3_bucket=item["s3_bucket"], s3_key=item["s3_key"])
    return VideoUrlResponse(
        triage_id=triage_id,
        video_url=video_url,
        content_type=item["content_type"],
        expires_in=settings.presigned_url_expiry_seconds,
    )
