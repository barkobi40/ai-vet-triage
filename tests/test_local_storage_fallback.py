"""
These tests deliberately do NOT use the `aws` fixture (see conftest.py) —
they exercise the real boto3 S3/DynamoDB clients with zero credentials
configured, exactly like `python main.py` on a laptop with no AWS setup,
to prove the local-disk + in-memory fallbacks in app/routers/triage.py,
app/db/dynamodb.py, and app/services/local_storage.py actually work end
to end rather than just not crashing.
"""
from fastapi.testclient import TestClient

from app.db import local_store
from app.main import app
from app.services.local_storage import STATIC_DIR


def test_upload_url_falls_back_to_local_storage_without_aws_credentials():
    local_store._items.clear()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/triage/upload-url",
            json={"pet_owner_description": "Limping since this morning", "content_type": "image/jpeg"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["s3_bucket"] == "local-storage"
    assert body["upload_url"] == f"/api/v1/triage/{body['triage_id']}/upload-local"


def test_full_local_upload_and_playback_round_trip():
    """Create a case, PUT bytes to the local fallback endpoint exactly like
    the dashboard's fetch() does, then confirm the file is really readable
    back through the /static mount — not just written to disk."""
    local_store._items.clear()
    file_bytes = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/triage/upload-url",
            json={"pet_owner_description": "test", "content_type": "image/jpeg"},
        )
        triage_id = create_response.json()["triage_id"]
        upload_url = create_response.json()["upload_url"]

        put_response = client.put(upload_url, content=file_bytes, headers={"Content-Type": "image/jpeg"})
        assert put_response.status_code == 204

        video_url_response = client.get(f"/api/v1/triage/{triage_id}/video-url")
        assert video_url_response.status_code == 200
        video_url = video_url_response.json()["video_url"]
        assert video_url.startswith("/static/uploads/")

        playback_response = client.get(video_url)

    assert playback_response.status_code == 200
    assert playback_response.content == file_bytes


def test_upload_local_404s_for_unknown_case():
    local_store._items.clear()
    with TestClient(app) as client:
        response = client.put("/api/v1/triage/does-not-exist/upload-local", content=b"data")

    assert response.status_code == 404


def teardown_module(_module):
    local_store._items.clear()
    for child in STATIC_DIR.glob("uploads/*"):
        for f in child.glob("*"):
            f.unlink()
        child.rmdir()
