import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Tracks WebSocket clients connected to *this* API process. Fan-out
    across multiple horizontally-scaled API instances happens one layer
    up — the Redis subscriber in app/ws/listener.py feeds every instance's
    manager.broadcast(), so each instance only needs to worry about its
    own locally-connected sockets.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        logger.info("WebSocket connected (%d active)", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        logger.info("WebSocket disconnected (%d active)", len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for connection in self._connections:
            try:
                await connection.send_json(message)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


manager = ConnectionManager()
