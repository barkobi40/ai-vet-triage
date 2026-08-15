import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.routers import triage, vets, ws
from app.services.local_storage import STATIC_DIR
from app.ws.listener import run_listener

settings = get_settings()
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Backs the local-disk upload fallback (app/services/local_storage.py) —
# must exist before StaticFiles mounts it below, since StaticFiles checks
# for the directory at construction time.
STATIC_DIR.mkdir(parents=True, exist_ok=True)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    listener_task = asyncio.create_task(run_listener())
    yield
    listener_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await listener_task


app = FastAPI(
    title="AI Vet-Triage",
    description="Asynchronous, event-driven veterinary triage platform.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(triage.router, prefix=settings.api_v1_prefix)
app.include_router(vets.router, prefix=settings.api_v1_prefix)
app.include_router(ws.router)  # /ws/triage — not versioned under api_v1_prefix by design
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/dashboard", tags=["dashboard"])
async def dashboard() -> FileResponse:
    """
    Serves web/dashboard.html same-origin with the API (http://localhost:8000
    for both the page and ws://localhost:8000/ws/triage), instead of opening
    the file directly via file://. That matters: browsers (Safari in
    particular) restrict outbound network requests — including the
    WebSocket handshake — from pages loaded via file://, which silently
    breaks the live-update demo with no server-side error to point at
    (confirmed: the server correctly reports 0 recipients because the
    WebSocket connection never actually completes on the client side).
    Same-origin sidesteps that restriction entirely rather than working
    around it with CORS headers, which govern read access to cross-origin
    responses and wouldn't affect whether the browser attempts the
    connection at all.
    """
    return FileResponse(WEB_DIR / "dashboard.html")


@app.get("/vet", tags=["dashboard"])
async def vet_dashboard() -> FileResponse:
    """Serves web/vet_dashboard.html same-origin, for the same reason as
    GET /dashboard above — the vet dashboard also opens a WebSocket."""
    return FileResponse(WEB_DIR / "vet_dashboard.html")
