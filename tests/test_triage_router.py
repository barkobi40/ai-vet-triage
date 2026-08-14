from decimal import Decimal

from fastapi.testclient import TestClient

from app.db import dynamodb, schema
from app.main import app


def test_upload_url_accepts_and_stores_owner_pet_fields(aws):
    settings = aws
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/triage/upload-url",
            json={
                "pet_owner_description": "Limping since this morning",
                "species": "Dog",
                "content_type": "video/mp4",
                "owner_name": "Jane Doe",
                "pet_name": "Rex",
                "pet_age": 3.5,
            },
        )

    assert response.status_code == 201
    triage_id = response.json()["triage_id"]

    item = dynamodb.get_table().get_item(Key={"PK": schema.triage_pk(triage_id), "SK": schema.TRIAGE_SK})["Item"]
    assert item["owner_name"] == "Jane Doe"
    assert item["pet_name"] == "Rex"
    assert item["pet_age"] == Decimal("3.5")  # stored as Decimal, not float — boto3 requires it


def test_upload_url_works_without_optional_owner_pet_fields(aws):
    """owner_name/pet_name/pet_age are optional — a request without them
    (e.g. from scripts/simulate_triage_update.py or any older client) must
    still succeed."""
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/triage/upload-url",
            json={
                "pet_owner_description": "Vomiting twice today",
                "content_type": "image/jpeg",
            },
        )

    assert response.status_code == 201


def test_video_url_returns_presigned_get_for_existing_case(aws):
    settings = aws
    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/triage/upload-url",
            json={"pet_owner_description": "test", "content_type": "video/webm"},
        )
        triage_id = create_response.json()["triage_id"]

        response = client.get(f"/api/v1/triage/{triage_id}/video-url")

    assert response.status_code == 200
    body = response.json()
    assert body["triage_id"] == triage_id
    assert body["content_type"] == "video/webm"
    assert "https://" in body["video_url"] or settings.s3_bucket_name in body["video_url"]


def test_video_url_404s_for_unknown_case(aws):
    with TestClient(app) as client:
        response = client.get("/api/v1/triage/does-not-exist/video-url")

    assert response.status_code == 404
