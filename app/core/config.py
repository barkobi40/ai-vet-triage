from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized app configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # AWS
    aws_region: str = "us-east-1"
    # None targets real AWS. Set to http://localhost:4566 for LocalStack in dev.
    aws_endpoint_url: str | None = None

    @field_validator("aws_endpoint_url", "redis_url", mode="before")
    @classmethod
    def _blank_env_value_means_unset(cls, v: str | None) -> str | None:
        # `.env` files conventionally leave an unused var as `KEY=` (empty
        # string), not omitted entirely — see this file's own "leave blank"
        # comments. Without this, pydantic-settings loads "" as a literal
        # empty string (not the None default), and boto3/redis both reject
        # an empty endpoint URL outright instead of treating it as unset.
        return v or None

    # S3
    s3_bucket_name: str = "ai-vet-triage-media-dev"
    presigned_url_expiry_seconds: int = 900  # 15 minutes

    # DynamoDB
    dynamodb_table_name: str = "vet-triage"

    # SQS
    sqs_queue_name: str = "vet-triage-processing"
    sqs_dlq_name: str = "vet-triage-processing-dlq"
    sqs_max_receive_count: int = 3  # deliveries before a message moves to the DLQ
    # Must exceed the AI worker's worst-case processing time (media upload +
    # Gemini multimodal triage call, including any key-rotation retries), or
    # SQS will redeliver a message to a second worker while the first is
    # still processing it.
    sqs_visibility_timeout_seconds: int = 300

    # Google Gemini (multimodal video/audio triage — see app/services/ai/).
    # Comma-separated so a quota-exhausted key can be rotated past without a
    # redeploy; kept as a plain str (not list[str]) because pydantic-settings
    # attempts JSON-decoding for complex env var types, which would reject a
    # bare comma-separated value like "key1,key2,key3".
    gemini_api_keys: str = ""  # e.g. "key1,key2,key3"
    # "-latest" alias (rather than a pinned version like "gemini-1.5-flash")
    # so this doesn't go stale when Google retires a model generation — that
    # exact failure mode (404 model-not-found, not an auth error) is what
    # surfaced during a live connectivity check of this project's keys.
    gemini_model: str = "gemini-flash-latest"

    # Redis (cross-process pub/sub fan-out for WebSocket updates). None disables
    # real-time push gracefully — DynamoDB writes still succeed either way.
    redis_url: str | None = None  # e.g. redis://localhost:6379/0
    redis_triage_updates_channel: str = "triage-updates"

    # App
    api_v1_prefix: str = "/api/v1"

    @property
    def gemini_api_key_list(self) -> list[str]:
        return [key.strip() for key in self.gemini_api_keys.split(",") if key.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
