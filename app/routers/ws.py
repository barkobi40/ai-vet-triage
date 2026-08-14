from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws.manager import manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/triage")
async def triage_updates(websocket: WebSocket) -> None:
    """
    Clinic dashboard clients connect here for live triage status/priority
    updates. In production these arrive via worker -> Redis -> every API
    instance's listener -> here (app/ws/listener.py). Locally, without
    Redis running, POST /ws/broadcast below reaches the same
    ConnectionManager directly for demo purposes.

    Push-only: the server never expects messages from the client. The
    receive loop below exists purely so Starlette notices when the client
    disconnects — there's no other signal for that on a pure server-push
    socket.
    """
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.post("/ws/broadcast")
async def broadcast_update(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Local-demo utility: broadcasts an arbitrary triage-update payload
    directly to this process's connected WebSocket clients, bypassing
    Redis entirely. This is what lets `python main.py` + `web/dashboard.html`
    demo the real-time push end-to-end with zero infrastructure (no Docker,
    no Redis) — see scripts/simulate_triage_update.py, which POSTs here.

    NOT a production substitute for Redis pub/sub: it only reaches clients
    connected to *this* process. In a horizontally-scaled deployment with
    multiple API replicas, a client connected to a different replica would
    never see this broadcast — that cross-process/cross-replica fan-out is
    exactly what Redis is for (see app/services/pubsub.py). This endpoint
    is also unauthenticated, so don't expose it on a public deployment
    without adding access control first.
    """
    await manager.broadcast(payload)
    return {"broadcast": True, "recipients": manager.connection_count}
