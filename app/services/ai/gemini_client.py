import logging
from typing import Callable, TypeVar

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AllGeminiKeysExhaustedError(RuntimeError):
    """
    Raised when every configured Gemini API key has hit ResourceExhausted.
    Deliberately a plain exception (not swallowed) so it propagates out of
    the worker's message handler and the SQS message is left un-acked —
    normal SQS redelivery / DLQ handling takes over from there.
    """


class GeminiKeyRotator:
    """
    Round-robins across a pool of Gemini API keys.

    The google-generativeai SDK has no per-call or per-model API key
    parameter — genai.configure(api_key=...) sets *global* module state
    (confirmed against the installed SDK: neither GenerativeModel.__init__
    nor genai.upload_file() accept an api_key). So "rotating" a key means
    re-calling genai.configure() with the next key before the next attempt,
    not swapping out a client object.
    """

    def __init__(self, api_keys: list[str]):
        if not api_keys:
            raise ValueError("At least one Gemini API key is required")
        self._keys = api_keys
        self._index = 0

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def current_key(self) -> str:
        return self._keys[self._index]

    def configure_current(self) -> None:
        genai.configure(api_key=self.current_key)

    def rotate(self) -> None:
        self._index = (self._index + 1) % len(self._keys)


def call_with_key_rotation(rotator: GeminiKeyRotator, fn: Callable[[], T]) -> T:
    """
    Configures the SDK with the rotator's current key and calls fn(). On
    ResourceExhausted (quota/rate limit), logs a warning, rotates to the
    next key, and retries — up to once per configured key. Any other
    exception propagates immediately without rotating (a quota error on
    key #2 doesn't mean key #3 is bad; a malformed request or a network
    error isn't fixed by switching keys).

    fn is a plain sync callable so this can wrap either genai.upload_file()
    or model.generate_content() (or both, in a single closure) — call sites
    decide what "one attempt" covers. Runs synchronously; callers on the
    asyncio event loop should invoke this inside asyncio.to_thread.
    """
    last_error: ResourceExhausted | None = None
    for attempt in range(1, len(rotator) + 1):
        rotator.configure_current()
        try:
            return fn()
        except ResourceExhausted as exc:
            last_error = exc
            logger.warning(
                "Gemini API key #%d/%d exhausted (ResourceExhausted); rotating to next key",
                attempt,
                len(rotator),
            )
            rotator.rotate()

    raise AllGeminiKeysExhaustedError(
        f"All {len(rotator)} configured Gemini API keys are exhausted"
    ) from last_error
