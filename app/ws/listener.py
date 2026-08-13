import asyncio
import logging

from app.core.config import get_settings
from app.services.pubsub import subscribe_triage_updates
from app.ws.manager import manager

logger = logging.getLogger(__name__)


async def run_listener() -> None:
    """
    Background task (started in app.main's lifespan): relays every Redis
    pub/sub message onto this process's locally-connected WebSocket
    clients. Reconnects with a short backoff if the Redis connection drops.
    A no-op if REDIS_URL isn't configured.
    """
    if not get_settings().redis_url:
        logger.warning(
            "REDIS_URL not configured; live dashboard push is disabled "
            "(DynamoDB writes still succeed — the dashboard just won't get a live update)."
        )
        return

    logger.info("Starting Redis pub/sub listener for real-time dashboard updates...")
    while True:
        try:
            async for update in subscribe_triage_updates():
                await manager.broadcast(update)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Redis pub/sub listener crashed; reconnecting in 5s...")
            await asyncio.sleep(5)
