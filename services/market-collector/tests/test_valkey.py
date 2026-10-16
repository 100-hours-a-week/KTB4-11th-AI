from ktb_aws.elasticache import ElastiCacheIAMProvider
from market_collector.settings import Settings
from market_collector.valkey import connect_valkey


def test_iam_connection_uses_shared_provider():
    settings = Settings(
        questdb_conf="unused",
        kiwoom_app_key="app",
        kiwoom_secret_key="secret",
        postgres_dsn="postgresql://unused",
        valkey_host="localhost",
        valkey_auth_mode="iam",
        valkey_username="backend",
        valkey_cache_name="my-cache",
    )

    kwargs = connect_valkey(settings).connection_pool.connection_kwargs

    assert isinstance(kwargs["credential_provider"], ElastiCacheIAMProvider)
