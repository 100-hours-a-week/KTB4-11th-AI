from urllib.parse import urlencode

import botocore.session
from botocore.model import ServiceId
from botocore.signers import RequestSigner
from redis.credentials import CredentialProvider


class ElastiCacheIAMProvider(CredentialProvider):
    def __init__(self, user: str, cache_name: str, region: str) -> None:
        self._user = user
        self._cache_name = cache_name
        self._region = region
        session = botocore.session.get_session()
        self._signer = RequestSigner(
            ServiceId("elasticache"),
            region,
            "elasticache",
            "v4",
            session.get_credentials(),
            session.get_component("event_emitter"),
        )

    def get_credentials(self) -> tuple[str, str]:
        query = urlencode(
            {"Action": "connect", "User": self._user, "ResourceType": "ServerlessCache"}
        )
        url = self._signer.generate_presigned_url(
            {
                "method": "GET",
                "url": f"https://{self._cache_name}/?{query}",
                "body": {},
                "headers": {},
                "context": {},
            },
            operation_name="connect",
            expires_in=900,
            region_name=self._region,
        )
        return self._user, url.removeprefix("https://")

    async def get_credentials_async(self) -> tuple[str, str]:
        return self.get_credentials()
