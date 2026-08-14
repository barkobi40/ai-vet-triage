from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TriageStatus(StrEnum):
    PENDING = "PENDING"        # record created, waiting for the client to finish the S3 upload
    UPLOADED = "UPLOADED"      # S3 ObjectCreated event confirmed by Lambda
    PROCESSING = "PROCESSING"  # worker picked up the SQS message
    COMPLETE = "COMPLETE"      # AI triage result written
    FAILED = "FAILED"          # exhausted retries / moved to DLQ


class Priority(StrEnum):
    PENDING = "PENDING"  # placeholder until the AI worker classifies the case
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"


# Maps accepted upload content types to the file extension used for the S3 key.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/wav": "wav",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


class UploadUrlRequest(BaseModel):
    """Payload the client sends before uploading media directly to S3."""

    pet_owner_description: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Free-text description of the pet's symptoms.",
    )
    species: Optional[str] = Field(default=None, max_length=100)
    content_type: str = Field(
        ..., description=f"Must be one of: {', '.join(ALLOWED_CONTENT_TYPES)}"
    )
    # Optional case-tracking metadata from the web dashboard's local owner/pet
    # profile (see web/dashboard.html). Not required by the triage pipeline
    # itself (Gemini only needs pet_owner_description + species + the media),
    # but the vet dashboard's case review panel displays these when present.
    owner_name: Optional[str] = Field(default=None, max_length=200)
    pet_name: Optional[str] = Field(default=None, max_length=200)
    pet_age: Optional[float] = Field(default=None, ge=0)

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        if v not in ALLOWED_CONTENT_TYPES:
            raise ValueError(
                f"Unsupported content_type '{v}'. Allowed: {sorted(ALLOWED_CONTENT_TYPES)}"
            )
        return v


class UploadUrlResponse(BaseModel):
    triage_id: str
    upload_url: str
    s3_key: str
    s3_bucket: str
    expires_in: int
    status: TriageStatus


class VideoUrlResponse(BaseModel):
    triage_id: str
    video_url: str
    content_type: str
    expires_in: int


# The exact disclaimer text the triage LLM must return. OpenAI's Structured
# Outputs could pin this with a `const` schema field; Gemini's response_schema
# dialect has no `const`, so it's enforced here instead via a field_validator —
# a wrong/missing disclaimer fails pydantic validation (and the case) rather
# than silently passing through. app/services/ai/prompts.py imports this
# constant so the prompt text and the enforced value can't drift apart.
DISCLAIMER_TEXT = (
    "AI-generated triage suggestion. Must be confirmed by licensed "
    "veterinary staff before any clinical action."
)


class TriageResult(BaseModel):
    """Mirrors app/services/ai/prompts.py:GEMINI_TRIAGE_RESPONSE_SCHEMA — this
    is what validates the LLM's structured-output response."""

    priority: Priority
    confidence: float = Field(ge=0, le=1)
    summary: str
    risk_factors: list[str]
    next_steps: list[str]
    species_detected: str
    requires_human_review: bool
    disclaimer: str

    @field_validator("priority")
    @classmethod
    def priority_must_be_definitive(cls, v: Priority) -> Priority:
        if v == Priority.PENDING:
            raise ValueError("Triage LLM must return RED/YELLOW/GREEN, not PENDING")
        return v

    @field_validator("disclaimer")
    @classmethod
    def disclaimer_must_match_exactly(cls, v: str) -> str:
        if v != DISCLAIMER_TEXT:
            raise ValueError(f"disclaimer must be exactly {DISCLAIMER_TEXT!r}, got {v!r}")
        return v
