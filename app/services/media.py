import asyncio
from pathlib import Path

from app.services.s3 import get_s3_client


async def download_media(bucket: str, key: str, dest_dir: Path) -> Path:
    local_path = dest_dir / Path(key).name
    client = get_s3_client()
    await asyncio.to_thread(client.download_file, bucket, key, str(local_path))
    return local_path
