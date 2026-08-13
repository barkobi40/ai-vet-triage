import pytest
from google.api_core.exceptions import ResourceExhausted

from app.core.config import get_settings
from app.services.ai import triage_llm

VALID_RESPONSE_JSON = """
{
  "priority": "RED",
  "confidence": 0.95,
  "summary": "Dog shows labored breathing and visible distress.",
  "risk_factors": ["labored breathing", "collapse observed in video"],
  "next_steps": ["Immediate in-person emergency exam"],
  "species_detected": "dog",
  "requires_human_review": true,
  "disclaimer": "AI-generated triage suggestion. Must be confirmed by licensed veterinary staff before any clinical action."
}
"""


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeModel:
    """Stands in for genai.GenerativeModel. A fresh instance is created on
    every retry attempt (matching triage_llm.py's real _attempt() closure),
    but all instances in a test share the same call_log list, so
    fail_first_n counts total attempts across the whole rotation, not
    per-instance."""

    def __init__(self, model_name, system_instruction, call_log, fail_first_n):
        self.model_name = model_name
        self.system_instruction = system_instruction
        self._call_log = call_log
        self._fail_first_n = fail_first_n

    def generate_content(self, contents, generation_config):
        self._call_log.append(contents)
        if len(self._call_log) <= self._fail_first_n:
            raise ResourceExhausted("quota exceeded (simulated)")
        return _FakeResponse(VALID_RESPONSE_JSON)


@pytest.fixture(autouse=True)
def _reset_module_level_rotator_cache(monkeypatch):
    # triage_llm caches a module-level rotator lazily; force each test to
    # rebuild it from whatever gemini_api_keys the test configures, instead
    # of reusing whatever a previous test happened to build.
    monkeypatch.setattr(triage_llm, "_rotator", None)


def _write_fake_media(tmp_path):
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"fake video bytes")
    return media_path


@pytest.mark.asyncio
async def test_run_triage_assessment_parses_valid_response_on_first_key(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "gemini_api_keys", "key-a,key-b")

    call_log: list = []
    configured_keys: list[str] = []
    monkeypatch.setattr(triage_llm.genai, "upload_file", lambda path: f"uploaded:{path}")
    monkeypatch.setattr(triage_llm.genai, "configure", lambda api_key: configured_keys.append(api_key))
    monkeypatch.setattr(
        triage_llm.genai,
        "GenerativeModel",
        lambda model_name, system_instruction: _FakeModel(
            model_name, system_instruction, call_log, fail_first_n=0
        ),
    )

    result = await triage_llm.run_triage_assessment(
        description="Dog collapsed and is breathing heavily",
        species="dog",
        media_path=_write_fake_media(tmp_path),
    )

    assert result.priority.value == "RED"
    assert result.confidence == 0.95
    assert result.requires_human_review is True
    assert configured_keys == ["key-a"]  # first key worked, no rotation needed


@pytest.mark.asyncio
async def test_run_triage_assessment_rotates_past_exhausted_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "gemini_api_keys", "key-a,key-b,key-c")

    call_log: list = []
    configured_keys: list[str] = []
    monkeypatch.setattr(triage_llm.genai, "upload_file", lambda path: f"uploaded:{path}")
    monkeypatch.setattr(triage_llm.genai, "configure", lambda api_key: configured_keys.append(api_key))
    monkeypatch.setattr(
        triage_llm.genai,
        "GenerativeModel",
        lambda model_name, system_instruction: _FakeModel(
            model_name, system_instruction, call_log, fail_first_n=2
        ),
    )

    result = await triage_llm.run_triage_assessment(
        description="Dog collapsed and is breathing heavily",
        species="dog",
        media_path=_write_fake_media(tmp_path),
    )

    assert result.priority.value == "RED"
    # key-a and key-b both hit ResourceExhausted; key-c succeeded.
    assert configured_keys == ["key-a", "key-b", "key-c"]


@pytest.mark.asyncio
async def test_run_triage_assessment_raises_when_no_keys_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "gemini_api_keys", "")

    with pytest.raises(ValueError):
        await triage_llm.run_triage_assessment(
            description="test", species=None, media_path=_write_fake_media(tmp_path)
        )
