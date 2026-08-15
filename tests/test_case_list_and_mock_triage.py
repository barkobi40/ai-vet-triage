import time

from fastapi.testclient import TestClient

from app.db import dynamodb, schema
from app.db import local_store
from app.main import app


def test_list_cases_scoped_to_owner_id(aws):
    with TestClient(app) as client:
        owner_a_response = client.post(
            "/api/v1/triage/upload-url",
            json={"pet_owner_description": "Owner A's case", "content_type": "image/jpeg", "owner_id": "owner-a"},
        )
        client.post(
            "/api/v1/triage/upload-url",
            json={"pet_owner_description": "Owner B's case", "content_type": "image/jpeg", "owner_id": "owner-b"},
        )

        response = client.get("/api/v1/triage", params={"owner_id": "owner-a"})

    assert response.status_code == 200
    cases = response.json()["cases"]
    assert len(cases) == 1
    assert cases[0]["triage_id"] == owner_a_response.json()["triage_id"]
    assert cases[0]["summary"] == "Owner A's case"


def test_list_cases_scoped_to_vet_id(aws):
    with TestClient(app) as client:
        client.post(
            "/api/v1/triage/upload-url",
            json={"pet_owner_description": "case 1", "content_type": "image/jpeg", "vet_id": "vet-1"},
        )
        client.post(
            "/api/v1/triage/upload-url",
            json={"pet_owner_description": "case 2", "content_type": "image/jpeg", "vet_id": "vet-2"},
        )

        response = client.get("/api/v1/triage", params={"vet_id": "vet-1"})

    assert response.status_code == 200
    cases = response.json()["cases"]
    assert len(cases) == 1
    assert cases[0]["summary"] == "case 1"


def test_list_cases_with_no_filter_returns_everything(aws):
    with TestClient(app) as client:
        client.post("/api/v1/triage/upload-url", json={"pet_owner_description": "a", "content_type": "image/jpeg"})
        client.post("/api/v1/triage/upload-url", json={"pet_owner_description": "b", "content_type": "image/jpeg"})

        response = client.get("/api/v1/triage")

    assert response.status_code == 200
    assert len(response.json()["cases"]) == 2


def test_list_cases_persists_across_a_fresh_request_simulating_page_refresh(aws):
    """The whole point of this endpoint: a case created in one request must
    still be visible in a completely separate later request — proving it's
    backed by real storage, not just in-memory client-side state."""
    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/triage/upload-url",
            json={"pet_owner_description": "persists across refresh", "content_type": "image/jpeg", "owner_id": "owner-x"},
        )
        triage_id = create_response.json()["triage_id"]

    # A brand new TestClient/app context — closest equivalent to a browser
    # refresh hitting the already-running server fresh.
    with TestClient(app) as client2:
        response = client2.get("/api/v1/triage", params={"owner_id": "owner-x"})

    cases = response.json()["cases"]
    assert any(c["triage_id"] == triage_id for c in cases)


def test_local_mode_case_gets_mock_completed_without_aws():
    """No `aws` fixture — exercises the real no-credentials fallback path,
    same as tests/test_local_storage_fallback.py. Verifies the case
    actually transitions PENDING -> COMPLETE with a real RED/YELLOW/GREEN
    priority, not just that the background task doesn't crash."""
    local_store._items.clear()
    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/triage/upload-url",
            json={"pet_owner_description": "needs mock triage", "content_type": "image/jpeg"},
        )
        triage_id = create_response.json()["triage_id"]

        item = local_store.get_item(schema.triage_pk(triage_id), schema.TRIAGE_SK)
        assert item["status"] == "PENDING"

        # TestClient drives the app synchronously via httpx, but the app's
        # own asyncio.create_task background task still needs real
        # wall-clock time to fire, so poll for it rather than asserting
        # immediately.
        for _ in range(40):
            item = local_store.get_item(schema.triage_pk(triage_id), schema.TRIAGE_SK)
            if item["status"] == "COMPLETE":
                break
            time.sleep(0.1)

    assert item["status"] == "COMPLETE"
    assert item["priority"] in ("RED", "YELLOW", "GREEN")
    assert "triage_result" in item
    assert item["triage_result"]["disclaimer"]

    local_store._items.clear()
