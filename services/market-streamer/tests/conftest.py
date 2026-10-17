import os

import pytest
import redis


@pytest.fixture
def valkey_url():
    url = os.environ.get("KTB_TEST_VALKEY_URL")
    if not url:
        pytest.skip("KTB_TEST_VALKEY_URL is not set")
    client = redis.Redis.from_url(url)
    client.flushdb()
    client.close()
    return url
