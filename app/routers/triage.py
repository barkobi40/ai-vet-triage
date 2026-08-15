from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.db import dynamodb, schema
from app.models.triage import (
    ALLOWED_CONTENT_TYPES,
    Priority,
    TriageStatus,
    UploadUrlRequest,
    UploadUrlResponse,
    VideoUrlResponse,
)
from app.services.s3 import generate_presigned_download_url, generate_presigned_upload_url

router = APIRouter(prefix="/triage", tags=["triage"])


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
         to S3 — the API server never touches the file bytes.
      3. Persist a PENDING record in DynamoDB so the case exists in the
         system (and is visible to the dashboard) before the upload even
         completes.
    """
    settings = get_settings()
    triage_id = str(uuid4())
    file_extension = ALLOWED_CONTENT_TYPES[payload.content_type]
    s3_key = f"uploads/{triage_id}/original.{file_extension}"

    upload_url = generate_presigned_upload_url(s3_key=s3_key, content_type=payload.content_type)

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
        "s3_bucket": settings.s3_bucket_name,
        "s3_key": s3_key,
        "content_type": payload.content_type,
        "created_at": now,
        "updated_at": now,
    }

    await dynamodb.put_item(item)

    return UploadUrlResponse(
        triage_id=triage_id,
        upload_url=upload_url,
        s3_key=s3_key,
        s3_bucket=settings.s3_bucket_name,
        expires_in=settings.presigned_url_expiry_seconds,
        status=TriageStatus.PENDING,
    )


@router.get("/{triage_id}/video-url", response_model=VideoUrlResponse)
async def get_video_url(triage_id: str) -> VideoUrlResponse:
    """Used by the vet dashboard's embedded player: looks up the case's S3
    location and returns a short-lived presigned GET URL, rather than
    proxying the media through the API server."""
    settings = get_settings()
    item = await dynamodb.get_item(schema.triage_pk(triage_id), schema.TRIAGE_SK)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    video_url = generate_presigned_download_url(s3_bucket=item["s3_bucket"], s3_key=item["s3_key"])
    return VideoUrlResponse(
        triage_id=triage_id,
        video_url=video_url,
        content_type=item["content_type"],
        expires_in=settings.presigned_url_expiry_seconds,
    )
