import time

from fastapi.testclient import TestClient

from app.db import local_store, schema
from app.main import app


def _create_case(client, **overrides):
    payload = {"pet_owner_description": "Vomiting since last night", "content_type": "image/jpeg"}
    payload.update(overrides)
    return client.post("/api/v1/triage/upload-url", json=payload).json()["triage_id"]


def test_vet_response_persists_and_is_visible_on_refresh(aws):
    with TestClient(app) as client:
        triage_id = _create_case(client, owner_id="owner-1")

        patch_response = client.patch(
            f"/api/v1/triage/{triage_id}/vet-response",
            json={"status": "Approved for Video Call", "vet_response": "Please come in for an exam today."},
        )
        assert patch_response.status_code == 200
        assert patch_response.json()["recipients"] == 0

    # Fresh app/request context, simulating a page refresh.
    with TestClient(app) as client2:
        list_response = client2.get("/api/v1/triage", params={"owner_id": "owner-1"})

    case = list_response.json()["cases"][0]
    assert case["status"] == "Approved for Video Call"
    assert case["vet_response"] == "Please come in for an exam today."


def test_vet_response_404s_for_unknown_case(aws):
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/triage/does-not-exist/vet-response",
            json={"status": "In Review", "vet_response": ""},
        )
    assert response.status_code == 404


def test_vet_response_preserves_other_case_fields(aws):
    """A vet response must not blank out summary/owner/pet fields — see
    the read-modify-write pattern in submit_vet_response."""
    with TestClient(app) as client:
        triage_id = _create_case(client, owner_name="Jane", pet_name="Rex", species="Dog")

        client.patch(
            f"/api/v1/triage/{triage_id}/vet-response",
            json={"status": "In Review", "vet_response": "Looking into it."},
        )
        response = client.get("/api/v1/triage")

    case = next(c for c in response.json()["cases"] if c["triage_id"] == triage_id)
    assert case["owner_name"] == "Jane"
    assert case["pet_name"] == "Rex"
    assert case["pet_owner_description"] == "Vomiting since last night"


def test_medical_record_pdf_downloads_for_owner(aws):
    with TestClient(app) as client:
        _create_case(client, owner_id="owner-pdf", owner_name="Jane", pet_name="Rex")
        client.post(
            "/api/v1/triage/upload-url",
            json={"pet_owner_description": "unrelated other owner's case", "content_type": "image/jpeg", "owner_id": "someone-else"},
        )

        response = client.get("/api/v1/triage/medical-record", params={"owner_id": "owner-pdf"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert "medical-record-owner-pdf.pdf" in response.headers["content-disposition"]


def test_medical_record_pdf_handles_no_cases(aws):
    with TestClient(app) as client:
        response = client.get("/api/v1/triage/medical-record", params={"owner_id": "nobody"})

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_mock_triage_summary_has_no_debug_prefix():
    """No `aws` fixture — exercises the real local-mode fallback, same as
    tests/test_local_storage_fallback.py."""
    local_store._items.clear()
    with TestClient(app) as client:
        triage_id = _create_case(client, owner_id="clean-summary-owner")

        item = None
        for _ in range(40):
            item = local_store.get_item(schema.triage_pk(triage_id), schema.TRIAGE_SK)
            if item["status"] == "COMPLETE":
                break
            time.sleep(0.1)

    assert item["status"] == "COMPLETE"
    summary = item["triage_result"]["summary"]
    assert "[Local-mode mock triage" not in summary
    assert "AWS/Gemini" not in summary
    assert summary.startswith("Initial AI Assessment:")

    local_store._items.clear()
