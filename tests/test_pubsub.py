import asyncio

import fakeredis
import pytest

from app.services import pubsub
from app.ws.listener import run_listener
from app.ws.manager import ConnectionManager


@pytest.fixture
def fake_redis(monkeypatch):
    """
    Real Redis isn't available in this environment, so this validates the
    actual publish/subscribe wiring against fakeredis's async client
    instead of just trusting the code compiles. get_redis_client is
    lru_cache'd in app.services.pubsub, so we monkeypatch the function
    itself rather than fight the cache.
    """
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(pubsub, "get_redis_client", lambda: client)

    from app.core.config import get_settings

    # run_listener() checks settings.redis_url before it will even attempt
    # to subscribe, so the fixture must set this too, not just stub the client.
    monkeypatch.setattr(get_settings(), "redis_url", "redis://fake-for-tests")
    return client


@pytest.mark.asyncio
async def test_publish_and_subscribe_round_trip(fake_redis):
    received = []

    async def consume():
        async for message in pubsub.subscribe_triage_updates():
            received.append(message)
            break

    consumer_task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)  # let the subscription register before publishing

    payload = {"triage_id": "abc-123", "status": "COMPLETE", "priority": "RED"}
    await pubsub.publish_triage_update(payload)

    await asyncio.wait_for(consumer_task, timeout=2)
    assert received == [payload]


@pytest.mark.asyncio
async def test_listener_relays_redis_messages_to_connected_websockets(fake_redis, monkeypatch):
    """End-to-end for the whole WS fan-out chain: publish_triage_update()
    (what the worker calls) -> Redis -> run_listener() -> ConnectionManager
    .broadcast() -> a connected client."""
    test_manager = ConnectionManager()
    monkeypatch.setattr("app.ws.listener.manager", test_manager)

    sent: list[dict] = []

    class _FakeWebSocket:
        async def send_json(self, message: dict) -> None:
            sent.append(message)

    test_manager._connections = {_FakeWebSocket()}

    listener_task = asyncio.create_task(run_listener())
    await asyncio.sleep(0.1)  # let the listener subscribe before we publish

    payload = {"triage_id": "xyz-789", "status": "COMPLETE", "priority": "YELLOW"}
    await pubsub.publish_triage_update(payload)

    for _ in range(20):
        if sent:
            break
        await asyncio.sleep(0.05)

    listener_task.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await listener_task

    assert sent == [payload]


@pytest.mark.asyncio
async def test_publish_triage_update_does_not_raise_when_redis_is_configured_but_unreachable(monkeypatch):
    """REDIS_URL pointing at a real, configured-but-dead host (unlike
    redis_url=None, which is the already-covered no-op path) must never
    surface as a failure of the caller's request — see app/routers/triage.py,
    where this is called inline during case creation."""
    import redis.asyncio as redis

    from app.services import pubsub

    dead_client = redis.from_url(
        "redis://localhost:1",  # nothing listens on port 1
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
    )
    monkeypatch.setattr(pubsub, "get_redis_client", lambda: dead_client)

    await pubsub.publish_triage_update({"triage_id": "unreachable-redis-test"})
