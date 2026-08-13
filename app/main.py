import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.core.config import get_settings
from app.routers import triage, ws
from app.ws.listener import run_listener

settings = get_settings()


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
app.include_router(ws.router)  # /ws/triage — not versioned under api_v1_prefix by design


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
