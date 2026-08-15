"""
Fires a fake triage-update event at a running FastAPI server so you can see
the WebSocket push working end-to-end (POST /ws/broadcast -> ConnectionManager
-> browser) with zero infrastructure — no Docker, no Redis, no AWS, no
Gemini credentials. Just `python main.py` running and web/dashboard.html
open in a browser.

This talks directly to the API process over plain HTTP (stdlib urllib, no
extra dependency), not Redis — see app/routers/ws.py:broadcast_update for
why that's the right choice for a local demo specifically (it can't reach
a *different* API replica, which is fine for a single local `python
main.py` process, but is exactly why the real worker pipeline uses Redis
pub/sub instead — see app/services/pubsub.py).

Usage:
    python scripts/simulate_triage_update.py
    python scripts/simulate_triage_update.py --priority YELLOW
    python scripts/simulate_triage_update.py --priority GREEN --status PROCESSING
    python scripts/simulate_triage_update.py --url http://localhost:9000
    python scripts/simulate_triage_update.py --priority RED --pet-name Rex --species Dog --pet-age 3
    python scripts/simulate_triage_update.py --priority RED --vet-id demo-vet --clinic-name "Demo Clinic"
"""
import argparse
import json
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone


def build_summary(priority: str, pet_name: str | None, species: str | None, pet_age: str | None) -> str:
    # Only use the pet-specific phrasing once *all three* details are given —
    # a summary with some placeholders filled and others missing would read
    # worse than just falling back to the generic demo text.
    if pet_name and species and pet_age:
        return f"[simulated] {priority} priority case: {pet_age} y/o {species} named {pet_name}."
    return f"[simulated] Example {priority.lower()}-priority case for WebSocket demo purposes."


def main(
    priority: str,
    status: str,
    base_url: str,
    pet_name: str | None = None,
    species: str | None = None,
    pet_age: str | None = None,
    vet_id: str | None = None,
    clinic_name: str | None = None,
) -> None:
    payload = {
        "triage_id": str(uuid.uuid4()),
        "status": status,
        "priority": priority,
        "summary": build_summary(priority, pet_name, species, pet_age),
        "confidence": 0.91,
        "requires_human_review": priority == "RED",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "vet_id": vet_id,
        "clinic_name": clinic_name,
    }

    url = f"{base_url.rstrip('/')}/ws/broadcast"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read())
    except urllib.error.URLError as exc:
        print(
            f"Could not reach {url} ({exc}).\n"
            f"Is the FastAPI server running? Start it with: python main.py"
        )
        raise SystemExit(1)

    print(f"Broadcast sent: {payload}")
    if result["recipients"] == 0:
        print(
            "No WebSocket clients were connected to receive it — open "
            "web/dashboard.html in a browser first, then re-run this script."
        )
    else:
        print(f"Delivered to {result['recipients']} connected dashboard client(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--priority", choices=["RED", "YELLOW", "GREEN", "PENDING"], default="RED")
    parser.add_argument("--status", choices=["PROCESSING", "COMPLETE", "FAILED"], default="COMPLETE")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of the running FastAPI server")
    parser.add_argument("--pet-name", default=None, help="e.g. Rex — used in the summary text if all three --pet-* flags are given")
    parser.add_argument("--species", default=None, help="e.g. Dog")
    parser.add_argument("--pet-age", default=None, help="e.g. 3")
    parser.add_argument("--vet-id", default=None, help="e.g. demo-vet — matches the Demo Vet quick-login's vetId")
    parser.add_argument("--clinic-name", default=None, help="e.g. Demo Clinic")
    args = parser.parse_args()
    main(
        args.priority,
        args.status,
        args.url,
        args.pet_name,
        args.species,
        args.pet_age,
        args.vet_id,
        args.clinic_name,
    )
