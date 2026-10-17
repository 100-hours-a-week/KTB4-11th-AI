from ktb_aws.elasticache import ElastiCacheIAMProvider
from redis import Redis

from market_collector.settings import Settings


def connect_valkey(settings: Settings) -> Redis:
    provider = None
    if (
        settings.valkey_auth_mode == "iam"
        and settings.valkey_username
        and settings.valkey_cache_name
    ):
        provider = ElastiCacheIAMProvider(
            settings.valkey_username, settings.valkey_cache_name, settings.aws_region
        )
    return Redis(
        host=settings.valkey_host,
        port=settings.valkey_port,
        ssl=settings.valkey_tls,
        credential_provider=provider,
        decode_responses=True,
    )
