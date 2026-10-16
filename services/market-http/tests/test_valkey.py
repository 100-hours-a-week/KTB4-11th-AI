import gc

from market_http.valkey import ElastiCacheIAMProvider


def test_iam_token_survives_garbage_collection(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    provider = ElastiCacheIAMProvider("backend", "my-cache", "ap-northeast-2")

    gc.collect()

    assert provider.get_credentials()[1].startswith("my-cache/?")
