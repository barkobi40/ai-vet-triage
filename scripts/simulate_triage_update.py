"""
Publishes a fake triage-update message to Redis so you can see the
WebSocket fan-out working end-to-end (Redis -> app.ws.listener ->
ConnectionManager -> browser) without running the full worker pipeline —
no AWS or Gemini credentials needed. Just Redis + the FastAPI app running,
and web/dashboard.html open in a browser.

Usage:
    python scripts/simulate_triage_update.py
    python scripts/simulate_triage_update.py --priority YELLOW
    python scripts/simulate_triage_update.py --priority GREEN --status PROCESSING
"""
import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.services.pubsub import publish_triage_update


async def main(priority: str, status: str) -> None:
    if not get_settings().redis_url:
        print(
            "REDIS_URL is not set in your environment/.env — publish_triage_update() "
            "will no-op. Set REDIS_URL=redis://localhost:6379/0 and make sure Redis "
            "is running (e.g. `docker run -p 6379:6379 redis` or `redis-server`)."
        )

    payload = {
        "triage_id": str(uuid.uuid4()),
        "status": status,
        "priority": priority,
        "summary": f"[simulated] Example {priority.lower()}-priority case for WebSocket demo purposes.",
        "confidence": 0.91,
        "requires_human_review": priority == "RED",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await publish_triage_update(payload)
    print(f"Published: {payload}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--priority", choices=["RED", "YELLOW", "GREEN", "PENDING"], default="RED")
    parser.add_argument("--status", choices=["PROCESSING", "COMPLETE", "FAILED"], default="COMPLETE")
    args = parser.parse_args()
    asyncio.run(main(args.priority, args.status))
