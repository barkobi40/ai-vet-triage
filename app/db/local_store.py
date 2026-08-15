"""
In-memory fallback store, used by app/db/dynamodb.py only when DynamoDB
itself is unreachable (no AWS credentials, no LocalStack, etc. — the
common case for running `python main.py` on a laptop with zero AWS setup).

Mirrors just the PK/SK/GSI1PK/GSI1SK/GSI2PK item shape used by the real
table — this is not a general DynamoDB emulator. It only backs put_item/
get_item/query_gsi1/get_by_gsi2, which is everything case submission, the
vet directory, and account login need; the AI worker's conditional
update_item() calls (idempotent claiming, writing triage results) aren't
covered here, since the worker needs SQS + Gemini regardless — both
unavailable in the same "no AWS" scenario, so there's nothing this
fallback could unblock there anyway.

Does not persist across process restarts. A deployed environment should
always have real AWS credentials; local integration testing that needs
the full pipeline should use LocalStack (see docker-compose.yml) instead
of relying on this.
"""
from typing import Any

_items: dict[tuple[str, str], dict[str, Any]] = {}


def put_item(item: dict[str, Any]) -> None:
    _items[(item["PK"], item["SK"])] = dict(item)


def get_item(pk: str, sk: str) -> dict[str, Any] | None:
    item = _items.get((pk, sk))
    return dict(item) if item is not None else None


def query_gsi1(gsi1pk: str, *, scan_index_forward: bool = True) -> list[dict[str, Any]]:
    matches = [dict(item) for item in _items.values() if item.get("GSI1PK") == gsi1pk]
    matches.sort(key=lambda item: item.get("GSI1SK", ""), reverse=not scan_index_forward)
    return matches


def get_by_gsi2(gsi2pk: str) -> dict[str, Any] | None:
    for item in _items.values():
        if item.get("GSI2PK") == gsi2pk:
            return dict(item)
    return None
