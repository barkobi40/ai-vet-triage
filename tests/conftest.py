import boto3
import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def _reset_lru_caches():
    """
    get_settings(), pubsub.get_redis_client(), dynamodb.get_table()/
    get_dynamodb_resource(), and s3.get_s3_client() are all @lru_cache'd
    with no arguments — correct for a long-lived production process, but
    it means whichever test runs first "locks in" a cached value for every
    test that runs after it in the same pytest process, regardless of any
    later monkeypatching. Concretely: a boto3 client that first resolved
    credentials outside an active moto mock_aws() context (see
    tests/test_local_storage_fallback.py, which deliberately runs without
    the `aws` fixture to exercise the no-AWS fallback path) caches that
    "no credentials" resolution, and a *later* test that does use the
    `aws` fixture would otherwise still get NoCredentialsError from the
    same stale cached client. Clearing every one of these before and after
    each test keeps the suite hermetic and independent of execution order.
    """
    from app.core.config import get_settings
    from app.db.dynamodb import get_dynamodb_resource, get_table
    from app.services.pubsub import get_redis_client
    from app.services.s3 import get_s3_client

    caches = (get_settings, get_redis_client, get_dynamodb_resource, get_table, get_s3_client)
    for cache in caches:
        cache.cache_clear()
    yield
    for cache in caches:
        cache.cache_clear()


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
