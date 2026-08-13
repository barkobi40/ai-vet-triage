import boto3
import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def _reset_lru_caches():
    """
    get_settings() and pubsub.get_redis_client() are both @lru_cache'd with
    no arguments — correct for a long-lived production process, but it
    means whichever test runs first "locks in" a cached value (e.g. a real
    Redis client resolved from a developer's local .env) for every test
    that runs after it in the same pytest process, regardless of any later
    monkeypatching. Clearing both before and after every test keeps the
    suite hermetic and independent of test execution order.
    """
    from app.core.config import get_settings
    from app.services.pubsub import get_redis_client

    get_settings.cache_clear()
    get_redis_client.cache_clear()
    yield
    get_settings.cache_clear()
    get_redis_client.cache_clear()


@pytest.fixture
def aws(monkeypatch):
    """
    Spins up mocked S3/DynamoDB/SQS via moto and provisions them exactly
    like scripts/create_table.py + scripts/create_queue.py do in real
    dev/prod, so tests exercise the same schema the app runs with.

    No real AWS credentials or network access required. Deliberately
    isolated from whatever local `.env` a developer happens to have on
    disk (e.g. a real REDIS_URL) — tests that need Redis configure it
    explicitly and controllably via `fakeredis` (see tests/test_pubsub.py).
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "redis_url", None)

        s3 = boto3.client("s3", region_name=settings.aws_region)
        s3.create_bucket(Bucket=settings.s3_bucket_name)

        from scripts.create_queue import create_queues
        from scripts.create_table import create_table

        create_table()
        create_queues()

        yield settings
