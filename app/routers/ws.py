from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws.manager import manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/triage")
async def triage_updates(websocket: WebSocket) -> None:
    """
    Clinic dashboard clients connect here for live triage status/priority
    updates (worker -> Redis -> every API instance's listener -> here).

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
