import httpx
import pytest
from news_preprocessor.sources.publishers.opendart.rss import OpenDartRSS


def test_company_rss_preserves_receipt_title_date_and_raw_item():
    def handle(req):
        assert req.url.host == "dart.fss.or.kr"
        assert dict(req.url.params) == {"crpCd": "00126380"}
        return httpx.Response(
            200,
            text="<rss><channel><item>"
            "<title>(유가)삼성전자 - [기재정정]반기보고서 (2026.06)</title>"
            "<link>https://dart.fss.or.kr/api/link.jsp?rcpNo=20261030000001</link>"
            "<pubDate>Fri, 30 Oct 2026 16:24:00 GMT</pubDate></item></channel></rss>",
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        entries = OpenDartRSS(client, "00126380").entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.external_id == "20261030000001"
    assert entry.title == "(유가)삼성전자 - [기재정정]반기보고서 (2026.06)"
    assert entry.published_at.isoformat() == "2026-10-30T16:24:00+00:00"
    assert "[기재정정]반기보고서" in entry.raw_payload
    assert entry.url.endswith("rcpNo=20261030000001")


@pytest.mark.parametrize(
    "content", ["<html>점검 중</html>", "<result><status>020</status></result>"]
)
def test_a_non_feed_response_is_a_collection_failure(content):
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=content))
    ) as client:
        with pytest.raises(ValueError, match="invalid DART RSS"):
            OpenDartRSS(client, "00126380").entries()


def test_empty_feed_is_valid_and_bad_items_do_not_hide_valid_items():
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text="<rss><channel/></rss>"))
    ) as client:
        assert OpenDartRSS(client, "00126380").entries() == []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                text="<rss><channel><item><title>missing link</title></item>"
                "<item><title>사업보고서</title>"
                "<link>https://example.com/?rcpNo=20261030000001</link>"
                "<pubDate>Fri, 30 Oct 2026 08:24:00 GMT</pubDate></item>"
                "<item><title>사업보고서 (2025.12)</title>"
                "<link>https://dart.fss.or.kr/api/link.jsp?rcpNo=20261030000002</link>"
                "<pubDate>Fri, 30 Oct 2026 08:24:00 GMT</pubDate></item></channel></rss>",
            )
        )
    ) as client:
        assert [e.external_id for e in OpenDartRSS(client, "00126380").entries()] == [
            "20261030000002"
        ]
