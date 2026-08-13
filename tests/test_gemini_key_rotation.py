import pytest
from google.api_core.exceptions import ResourceExhausted

from app.services.ai.gemini_client import (
    AllGeminiKeysExhaustedError,
    GeminiKeyRotator,
    call_with_key_rotation,
)


def test_rotator_wraps_around_and_starts_at_first_key():
    rotator = GeminiKeyRotator(["key-a", "key-b", "key-c"])
    assert rotator.current_key == "key-a"
    rotator.rotate()
    assert rotator.current_key == "key-b"
    rotator.rotate()
    assert rotator.current_key == "key-c"
    rotator.rotate()
    assert rotator.current_key == "key-a"  # wraps back around


def test_rotator_rejects_empty_key_list():
    with pytest.raises(ValueError):
        GeminiKeyRotator([])


def test_call_with_rotation_succeeds_on_first_key_without_rotating(monkeypatch):
    rotator = GeminiKeyRotator(["key-a", "key-b"])
    configured_keys: list[str] = []
    monkeypatch.setattr(rotator, "configure_current", lambda: configured_keys.append(rotator.current_key))

    result = call_with_key_rotation(rotator, lambda: "ok")

    assert result == "ok"
    assert configured_keys == ["key-a"]
    assert rotator.current_key == "key-a"  # never rotated


def test_call_with_rotation_rotates_past_exhausted_keys_then_succeeds(monkeypatch):
    rotator = GeminiKeyRotator(["key-a", "key-b", "key-c"])
    configured_keys: list[str] = []
    monkeypatch.setattr(rotator, "configure_current", lambda: configured_keys.append(rotator.current_key))

    attempts = {"n": 0}

    def flaky_call() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ResourceExhausted(f"quota exceeded on attempt {attempts['n']}")
        return "success on third key"

    result = call_with_key_rotation(rotator, flaky_call)

    assert result == "success on third key"
    assert configured_keys == ["key-a", "key-b", "key-c"]
    assert attempts["n"] == 3


def test_call_with_rotation_raises_when_all_keys_exhausted(monkeypatch):
    rotator = GeminiKeyRotator(["key-a", "key-b"])
    monkeypatch.setattr(rotator, "configure_current", lambda: None)

    def always_exhausted() -> str:
        raise ResourceExhausted("quota exceeded")

    with pytest.raises(AllGeminiKeysExhaustedError) as exc_info:
        call_with_key_rotation(rotator, always_exhausted)

    assert isinstance(exc_info.value.__cause__, ResourceExhausted)
    assert rotator.current_key == "key-a"  # index wrapped back to the start after the last rotation


def test_call_with_rotation_does_not_rotate_on_non_quota_errors(monkeypatch):
    """A ResourceExhausted on key #1 means key #1 is out of quota — that
    says nothing about whether key #2 is any good, so rotating makes sense.
    But a non-quota error (bad request, network failure, ...) isn't fixed
    by switching keys, so it must propagate immediately without retrying."""
    rotator = GeminiKeyRotator(["key-a", "key-b"])
    configured_keys: list[str] = []
    monkeypatch.setattr(rotator, "configure_current", lambda: configured_keys.append(rotator.current_key))

    def raises_unrelated_error() -> str:
        raise ValueError("malformed request, not a quota issue")

    with pytest.raises(ValueError):
        call_with_key_rotation(rotator, raises_unrelated_error)

    assert configured_keys == ["key-a"]  # only tried once, no rotation
