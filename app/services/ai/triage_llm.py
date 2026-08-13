import asyncio
from pathlib import Path

import google.generativeai as genai

from app.core.config import get_settings
from app.models.triage import TriageResult
from app.services.ai.gemini_client import GeminiKeyRotator, call_with_key_rotation
from app.services.ai.prompts import GEMINI_TRIAGE_RESPONSE_SCHEMA, SYSTEM_PROMPT

_rotator: GeminiKeyRotator | None = None


def _get_rotator() -> GeminiKeyRotator:
    """Lazily built (and cached) so import of this module never fails just
    because GEMINI_API_KEYS isn't set — only actually calling the triage
    LLM requires it, matching how get_settings() itself defers validation."""
    global _rotator
    if _rotator is None:
        _rotator = GeminiKeyRotator(get_settings().gemini_api_key_list)
    return _rotator


def _build_prompt(description: str, species: str | None) -> str:
    parts = [f"Pet owner description: {description}"]
    if species:
        parts.append(f"Species (owner-reported): {species}")
    return "\n".join(parts)


async def run_triage_assessment(*, description: str, species: str | None, media_path: Path) -> TriageResult:
    """
    Uploads the raw media file to Gemini and requests a structured triage
    assessment in a single call. Gemini 1.5 Flash processes video frames and
    the audio track natively, so — unlike the previous OpenAI-based
    pipeline — there's no separate Whisper transcription step and no
    ffmpeg frame extraction; the model watches/listens to the file directly.

    Wrapped in call_with_key_rotation: on ResourceExhausted (quota/rate
    limit) it rotates to the next configured Gemini API key and retries: see
    app/services/ai/gemini_client.py. If every key is exhausted,
    AllGeminiKeysExhaustedError propagates out of this call, which the
    worker leaves un-acked so SQS redelivery / DLQ handling takes over.
    """
    rotator = _get_rotator()

    def _attempt() -> str:
        uploaded_file = genai.upload_file(str(media_path))
        model = genai.GenerativeModel(
            model_name=get_settings().gemini_model,
            system_instruction=SYSTEM_PROMPT,
        )
        response = model.generate_content(
            [uploaded_file, _build_prompt(description, species)],
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=GEMINI_TRIAGE_RESPONSE_SCHEMA,
            ),
        )
        return response.text

    def _call() -> str:
        return call_with_key_rotation(rotator, _attempt)

    raw_json = await asyncio.to_thread(_call)
    return TriageResult.model_validate_json(raw_json)
