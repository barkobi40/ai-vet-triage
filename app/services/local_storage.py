"""
Local-disk fallback for uploaded media, used only when S3 itself is
unreachable (see app/services/s3.py and app/routers/triage.py) — i.e.
running `python main.py` with no AWS credentials configured at all.

Files are saved under STATIC_DIR / s3_key (the same "uploads/{triage_id}/
original.{ext}" key already used for the real S3 object), and STATIC_DIR
is mounted at /static by app/main.py, so a saved file is immediately
servable back to the browser without the API ever proxying the bytes
itself on the way out.
"""
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


def save_file(s3_key: str, content: bytes) -> Path:
    dest = STATIC_DIR / s3_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return dest


def local_url_for(s3_key: str) -> str:
    return f"/static/{s3_key}"
