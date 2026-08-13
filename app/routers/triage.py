from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, status

from app.core.config import get_settings
from app.db import dynamodb, schema
from app.models.triage import (
    ALLOWED_CONTENT_TYPES,
    Priority,
    TriageStatus,
    UploadUrlRequest,
    UploadUrlResponse,
)
from app.services.s3 import generate_presigned_upload_url

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
