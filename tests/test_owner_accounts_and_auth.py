from fastapi.testclient import TestClient

from app.main import app

OWNER_PAYLOAD = {
    "owner_name": "Alex Owner",
    "email": "alex@example.com",
    "phone": "555-0100",
    "password": "correct-horse-battery",
    "pet_name": "Buddy",
    "species": "Dog",
    "breed": "Labrador",
    "pet_age": 4,
    "pet_weight": 28.5,
    "vet_id": "demo-vet",
    "clinic_name": "Demo Clinic",
}


def test_register_owner_returns_account_without_password(aws):
    with TestClient(app) as client:
        response = client.post("/api/v1/owners/register", json=OWNER_PAYLOAD)

    assert response.status_code == 201
    account = response.json()["account"]
    assert account["owner_name"] == "Alex Owner"
    assert account["email"] == "alex@example.com"
    assert account["pet_name"] == "Buddy"
    assert "password" not in account
    assert "password_hash" not in account


def test_register_owner_hashes_password(aws):
    with TestClient(app) as client:
        response = client.post("/api/v1/owners/register", json=OWNER_PAYLOAD)

    owner_id = response.json()["account"]["owner_id"]

    from app.db import dynamodb, schema

    item = dynamodb.get_table().get_item(Key={"PK": schema.owner_pk(owner_id), "SK": schema.OWNER_SK})["Item"]
    assert "password" not in item
    assert item["password_hash"] != OWNER_PAYLOAD["password"]


def test_register_owner_rejects_duplicate_email(aws):
    with TestClient(app) as client:
        first = client.post("/api/v1/owners/register", json=OWNER_PAYLOAD)
        second = client.post("/api/v1/owners/register", json={**OWNER_PAYLOAD, "owner_name": "Someone Else"})

    assert first.status_code == 201
    assert second.status_code == 409


def test_owner_can_log_in_with_correct_credentials(aws):
    with TestClient(app) as client:
        client.post("/api/v1/owners/register", json=OWNER_PAYLOAD)
        response = client.post("/api/v1/auth/login", json={"email": "alex@example.com", "password": "correct-horse-battery"})

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "owner"
    assert body["owner"]["pet_name"] == "Buddy"
    assert body["vet"] is None


def test_login_rejects_wrong_password(aws):
    with TestClient(app) as client:
        client.post("/api/v1/owners/register", json=OWNER_PAYLOAD)
        response = client.post("/api/v1/auth/login", json={"email": "alex@example.com", "password": "wrong-password"})

    assert response.status_code == 401


def test_login_rejects_unknown_email(aws):
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever123"})

    assert response.status_code == 401


def test_login_auto_detects_vet_role(aws):
    vet_payload = {
        "vet_name": "Dr. Vet",
        "clinic_name": "Vet Clinic",
        "clinic_address": "1 Main St",
        "phone": "555-0200",
        "license_number": "VET-1",
        "username": "drvet",
        "email": "vet@example.com",
        "password": "vet-password-1",
    }
    with TestClient(app) as client:
        client.post("/api/v1/vets/register", json=vet_payload)
        response = client.post("/api/v1/auth/login", json={"email": "vet@example.com", "password": "vet-password-1"})

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "vet"
    assert body["vet"]["clinic_name"] == "Vet Clinic"
    assert body["owner"] is None


def test_update_owner_profile_persists_and_preserves_vet_id(aws):
    with TestClient(app) as client:
        register_response = client.post("/api/v1/owners/register", json=OWNER_PAYLOAD)
        owner_id = register_response.json()["account"]["owner_id"]

        patch_response = client.patch(
            f"/api/v1/owners/{owner_id}",
            json={
                "owner_name": "Alex Owner",
                "pet_name": "Buddy",
                "species": "Dog",
                "breed": "Golden Retriever",
                "pet_age": 5,
                "pet_weight": 30,
            },
        )

    assert patch_response.status_code == 200
    updated = patch_response.json()
    assert updated["breed"] == "Golden Retriever"
    assert updated["pet_age"] == 5
    # vet_id is fixed at registration, not editable via this endpoint
    assert updated["vet_id"] == "demo-vet"


def test_update_owner_404s_for_unknown_owner(aws):
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/owners/does-not-exist",
            json={"owner_name": "X", "pet_name": "Y", "species": "Dog", "pet_age": 1},
        )
    assert response.status_code == 404
