import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ws.manager import ConnectionManager, manager


class _FakeWebSocket:
    """Stands in for a starlette WebSocket in pure unit tests of the fan-out
    logic — no ASGI transport / event loop plumbing required."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise RuntimeError("simulated dead connection")
        self.sent.append(message)


@pytest.mark.asyncio
async def test_broadcast_delivers_to_all_connections_and_prunes_dead_ones():
    mgr = ConnectionManager()
    alive_a = _FakeWebSocket()
    alive_b = _FakeWebSocket()
    dead = _FakeWebSocket(fail=True)
    mgr._connections = {alive_a, alive_b, dead}  # bypass connect() to skip the accept() handshake

    message = {"triage_id": "abc", "status": "COMPLETE", "priority": "RED"}
    await mgr.broadcast(message)

    assert alive_a.sent == [message]
    assert alive_b.sent == [message]
    assert dead not in mgr._connections  # pruned after send_json raised
    assert len(mgr._connections) == 2


def test_ws_route_accepts_connection_and_deregisters_on_disconnect():
    with TestClient(app) as client:
        assert len(manager._connections) == 0
        with client.websocket_connect("/ws/triage") as _websocket:
            assert len(manager._connections) == 1
        # Give the server-side WebSocketDisconnect handler a beat to run —
        # TestClient's client-side close doesn't block on server-side cleanup.
        for _ in range(20):
            if len(manager._connections) == 0:
                break
            time.sleep(0.05)
        assert len(manager._connections) == 0


def test_broadcast_endpoint_delivers_to_connected_websocket_client():
    """POST /ws/broadcast is what scripts/simulate_triage_update.py calls to
    demo the WebSocket layer without Redis — this is the actual HTTP
    contract that script depends on."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws/triage") as websocket:
            payload = {"triage_id": "sim-1", "status": "COMPLETE", "priority": "RED"}
            response = client.post("/ws/broadcast", json=payload)

            assert response.status_code == 200
            assert response.json() == {"broadcast": True, "recipients": 1}
            assert websocket.receive_json() == payload


def test_broadcast_endpoint_reports_zero_recipients_when_nobody_connected():
    with TestClient(app) as client:
        response = client.post("/ws/broadcast", json={"triage_id": "sim-2", "priority": "GREEN"})

        assert response.status_code == 200
        assert response.json() == {"broadcast": True, "recipients": 0}


def test_dashboard_route_serves_html_same_origin_as_the_websocket():
    """GET /dashboard exists specifically so the page and ws://.../ws/triage
    are same-origin — opening dashboard.html via file:// instead hits
    browser restrictions on outbound requests from file:// pages, which
    silently breaks the WebSocket handshake (confirmed against a real
    browser: the server correctly reports 0 recipients because the
    connection never completes client-side)."""
    with TestClient(app) as client:
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "ws/triage" in response.text
