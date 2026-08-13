import json
import logging
from functools import lru_cache
from typing import Any, AsyncIterator

import redis.asyncio as redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_redis_client() -> "redis.Redis | None":
    settings = get_settings()
    if not settings.redis_url:
        return None
    return redis.from_url(settings.redis_url, decode_responses=True)


async def publish_triage_update(payload: dict[str, Any]) -> None:
    """
    Publishes a triage status/priority update so every API instance's Redis
    subscriber (app/ws/listener.py) can fan it out to its own
    locally-connected WebSocket clients.

    An out-of-process broker is required here specifically because the
    worker and the API are separate processes (often separate hosts) — an
    in-memory broadcaster living inside one API process has no way to
    observe a message published from another process. If REDIS_URL isn't
    configured, this degrades to a no-op: the DynamoDB write the caller
    already made still stands, the dashboard just won't get a live push
    (it would need to poll/refresh instead).
    """
    client = get_redis_client()
    if client is None:
        logger.warning(
            "REDIS_URL not configured; skipping real-time broadcast for triage_id=%s",
            payload.get("triage_id"),
        )
        return
    await client.publish(get_settings().redis_triage_updates_channel, json.dumps(payload))


async def subscribe_triage_updates() -> AsyncIterator[dict[str, Any]]:
    """Yields decoded messages published to the triage-updates channel.
    Yields nothing (completes immediately) if Redis isn't configured."""
    client = get_redis_client()
    if client is None:
        return

    pubsub = client.pubsub()
    channel = get_settings().redis_triage_updates_channel
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                yield json.loads(message["data"])
            except json.JSONDecodeError:
                logger.warning("Dropping malformed pub/sub message: %r", message["data"])
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
