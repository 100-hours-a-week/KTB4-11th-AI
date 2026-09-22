import email
import io

import pytest


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type: str):
        super().__init__(body)
        self.headers = email.message_from_string(f"Content-Type: {content_type}")


@pytest.fixture
def fake_response():
    return FakeResponse
