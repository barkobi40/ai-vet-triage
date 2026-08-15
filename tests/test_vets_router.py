from fastapi.testclient import TestClient

from app.main import app

VET_PAYLOAD = {
    "vet_name": "Dr. Jane Smith",
    "clinic_name": "Sunrise Animal Hospital",
    "clinic_address": "123 Main St, Springfield",
    "phone": "555-0100",
    "license_number": "VET-12345",
    "username": "jsmith",
}


def test_register_vet_returns_vet_id(aws):
    with TestClient(app) as client:
        response = client.post("/api/v1/vets/register", json=VET_PAYLOAD)

    assert response.status_code == 201
    assert "vet_id" in response.json()


def test_register_vet_never_accepts_or_stores_a_password(aws):
    """VetRegisterRequest has no password field at all — sending one should
    just be ignored (extra fields aren't rejected by default), not stored."""
    with TestClient(app) as client:
        response = client.post("/api/v1/vets/register", json={**VET_PAYLOAD, "password": "hunter2"})

    assert response.status_code == 201
    vet_id = response.json()["vet_id"]

    from app.db import dynamodb, schema

    item = dynamodb.get_table().get_item(Key={"PK": schema.vet_pk(vet_id), "SK": schema.VET_SK})["Item"]
    assert "password" not in item


def test_list_vets_returns_registered_vets_sorted_by_clinic_name(aws):
    with TestClient(app) as client:
        client.post("/api/v1/vets/register", json={**VET_PAYLOAD, "clinic_name": "Westside Vet Clinic", "username": "west"})
        client.post("/api/v1/vets/register", json={**VET_PAYLOAD, "clinic_name": "Eastside Animal Care", "username": "east"})

        response = client.get("/api/v1/vets")

    assert response.status_code == 200
    clinics = [v["clinic_name"] for v in response.json()["vets"]]
    assert clinics == sorted(clinics)
    assert "Westside Vet Clinic" in clinics
    assert "Eastside Animal Care" in clinics


def test_list_vets_empty_when_none_registered(aws):
    with TestClient(app) as client:
        response = client.get("/api/v1/vets")

    assert response.status_code == 200
    assert response.json() == {"vets": []}


def test_upload_url_stores_vet_id_and_clinic_name(aws):
    with TestClient(app) as client:
        register_response = client.post("/api/v1/vets/register", json=VET_PAYLOAD)
        vet_id = register_response.json()["vet_id"]

        upload_response = client.post(
            "/api/v1/triage/upload-url",
            json={
                "pet_owner_description": "test",
                "content_type": "video/mp4",
                "vet_id": vet_id,
                "clinic_name": "Sunrise Animal Hospital",
            },
        )

    assert upload_response.status_code == 201
    triage_id = upload_response.json()["triage_id"]

    from app.db import dynamodb, schema

    item = dynamodb.get_table().get_item(Key={"PK": schema.triage_pk(triage_id), "SK": schema.TRIAGE_SK})["Item"]
    assert item["vet_id"] == vet_id
    assert item["clinic_name"] == "Sunrise Animal Hospital"
