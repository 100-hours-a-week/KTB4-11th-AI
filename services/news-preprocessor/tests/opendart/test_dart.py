from datetime import date

import httpx
import pytest
from ktb_core.logging import JsonFormatter
from news_preprocessor.sources.publishers.opendart.dart import DartClient, report_period


def test_fiscal_periods_are_not_receipt_dates():
    assert report_period("[기재정정]사업보고서 (2025.12)", 12) == (
        2025,
        "annual",
        date(2025, 12, 31),
    )
    assert report_period("반기보고서 (2025.06)", 12) == (2025, "half", date(2025, 6, 30))
    assert report_period("분기보고서 (2025.09)", 12) == (2025, "q3", date(2025, 9, 30))
    assert report_period("분기보고서 (2025.06)", 3) == (2026, "q1", date(2025, 6, 30))
    assert report_period("분기보고서 (2025.12)", 3) == (2026, "q3", date(2025, 12, 31))
    with pytest.raises(ValueError):
        report_period("분기보고서 (2025.12)", 12)


def test_official_detail_type_selects_the_periodic_kind():
    assert report_period("정기공시 (2025.12)", 12, "A001") == (2025, "annual", date(2025, 12, 31))
    assert report_period("정기공시 (2025.06)", 12, "A002") == (2025, "half", date(2025, 6, 30))
    assert report_period("정기공시 (2025.09)", 12, "A003") == (2025, "q3", date(2025, 9, 30))
    with pytest.raises(ValueError):
        report_period("반기보고서 (2025.06)", 12, "A001")


def test_nonperiodic_types_do_not_fabricate_fiscal_dates():
    assert report_period("감사보고서 (2025.12)", 12, "F001") == (2025, "F001", date(2025, 12, 31))
    assert report_period("감사보고서", 12, "F001") == (None, "F001", None)
    assert report_period("증권신고서", 12, "C001") == (None, "C001", None)


def test_api_error_does_not_expose_key():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"status": "020", "message": "key test-key"})
        )
    ) as client:
        with pytest.raises(RuntimeError) as error:
            DartClient(client, "test-key", interval=0, attempts=1).closing_month("00126380")
        assert "test-key" not in str(error.value)


def test_request_retry_logs_endpoint_attempt_status_and_receipt_without_key(caplog, monkeypatch):
    import logging

    monkeypatch.setattr(
        "news_preprocessor.sources.publishers.opendart.dart.time.sleep", lambda _: None
    )
    caplog.set_level(logging.WARNING)
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503))) as client:
        with pytest.raises(RuntimeError, match="DART request failed"):
            DartClient(client, "private-dart-key", interval=0, attempts=2).get(
                "document.xml", {"rcept_no": "20261030000001"}
            )
    records = [
        r for r in caplog.records if r.message in {"dart_request_retry", "dart_request_failed"}
    ]
    assert [r.message for r in records] == ["dart_request_retry", "dart_request_failed"]
    assert [r.levelname for r in records] == ["WARNING", "ERROR"]
    assert [r.fields["attempt"] for r in records] == [1, 2]
    for record in records:
        assert record.fields["endpoint"] == "document.xml"
        assert record.fields["receipt_id"] == "20261030000001"
        assert record.fields["http_status"] == 503
        assert record.fields["error_type"] == "HTTPStatusError"
        assert record.fields["max_attempts"] == 2
        assert "private-dart-key" not in JsonFormatter("test").format(record)


def test_api_status_failure_logs_financial_context_without_response_message(caplog):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"status": "020", "message": "private-dart-key"})
        )
    ) as client:
        with pytest.raises(RuntimeError, match="DART API status 020"):
            DartClient(client, "private-dart-key", interval=0, attempts=1).financial_statements(
                "00126380", 2026, "11012", "CFS"
            )
    records = [r for r in caplog.records if r.message == "dart_api_failed"]
    assert len(records) == 1
    fields = records[0].fields
    assert fields["endpoint"] == "fnlttSinglAcntAll.json"
    assert fields["dart_status"] == "020"
    assert fields["corp_code"] == "00126380"
    assert fields["business_year"] == "2026"
    assert fields["report_code"] == "11012"
    assert fields["scope"] == "CFS"
    assert "private-dart-key" not in JsonFormatter("test").format(records[0])


@pytest.mark.parametrize("endpoint", ["list.json", "document.xml"])
def test_request_recovers_on_third_attempt_without_logging_final_failure(
    monkeypatch, caplog, endpoint
):
    monkeypatch.setattr(
        "news_preprocessor.sources.publishers.opendart.dart.time.sleep", lambda _: None
    )
    requests = []

    def recover(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(503)
        if endpoint.endswith(".json"):
            return httpx.Response(200, json={"status": "013"})
        return httpx.Response(200, content=b"<result><status>014</status></result>")

    with httpx.Client(transport=httpx.MockTransport(recover)) as client:
        content = DartClient(client, "test-key", interval=0).get(endpoint, {})
    assert content
    assert len(requests) == 3
    assert [r.message for r in caplog.records].count("dart_request_retry") == 2
    assert not any(r.message in ("dart_request_failed", "dart_api_failed") for r in caplog.records)


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("list.json", b'{"status":"000"}'),
        ("list.json", b'{"status":"000","list":null,"total_page":1}'),
        ("list.json", b'{"status":"000","list":[{}],"total_page":1}'),
        ("list.json", b'{"status":"000","list":[],"total_page":"one"}'),
        ("fnlttSinglAcntAll.json", b'{"status":"000"}'),
        ("fnlttSinglAcntAll.json", b'{"status":"000","list":[null]}'),
        ("fnlttSinglAcntAll.json", b'{"status":"000","list":[{}]}'),
        ("fnlttSinglAcntAll.json", b'{"status":"000","list":[{"sj_div":123}]}'),
        ("fnlttSinglAcntAll.json", b'{"status":"000","list":[{"sj_div":"BS","ord":1}]}'),
        ("company.json", b'{"status":"000"}'),
        ("company.json", b'{"status":"000","acc_mt":"13"}'),
        ("document.xml", b"not a ZIP or DART status XML"),
        ("document.xml", b"PKbroken archive"),
        ("document.xml", b"<result><status>000</status></result>"),
        ("document.xml", b"<result/>"),
    ],
)
def test_invalid_success_responses_exhaust_three_attempts(endpoint, payload, monkeypatch, caplog):
    monkeypatch.setattr(
        "news_preprocessor.sources.publishers.opendart.dart.time.sleep", lambda _: None
    )
    requests = []

    def invalid(request):
        requests.append(request)
        return httpx.Response(200, content=payload)

    with httpx.Client(transport=httpx.MockTransport(invalid)) as client:
        with pytest.raises(RuntimeError, match="DART request failed"):
            DartClient(client, "test-key", interval=0).get(endpoint, {})
    assert len(requests) == 3
    final = [r for r in caplog.records if r.message == "dart_request_failed"]
    assert len(final) == 1
    assert final[0].fields["reason"] == "invalid_response"


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("list.json", b'{"status":"013"}'),
        ("fnlttSinglAcntAll.json", b'{"status":"013"}'),
        ("document.xml", b"<result><status>014</status></result>"),
    ],
)
def test_no_data_responses_return_without_retry(endpoint, payload):
    requests = []

    def empty(request):
        requests.append(request)
        return httpx.Response(200, content=payload)

    with httpx.Client(transport=httpx.MockTransport(empty)) as client:
        assert DartClient(client, "test-key", interval=0).get(endpoint, {}) == payload
    assert len(requests) == 1


def test_malformed_listing_recovers_on_third_valid_response(monkeypatch):
    monkeypatch.setattr(
        "news_preprocessor.sources.publishers.opendart.dart.time.sleep", lambda _: None
    )
    requests = []

    def recover(request):
        requests.append(request)
        return httpx.Response(
            200, json={"status": "000"} if len(requests) < 3 else {"status": "013"}
        )

    with httpx.Client(transport=httpx.MockTransport(recover)) as client:
        assert DartClient(client, "test-key", interval=0).json("list.json", {}) == {"status": "013"}
    assert len(requests) == 3


def test_corrupted_zip_members_exhaust_three_requests(monkeypatch):
    import io
    import zipfile

    monkeypatch.setattr(
        "news_preprocessor.sources.publishers.opendart.dart.time.sleep", lambda _: None
    )
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("main.xml", "<DOCUMENT><P>unique-body</P></DOCUMENT>")
    corrupt = data.getvalue().replace(b"unique-body", b"broken-body")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=corrupt)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RuntimeError, match="DART request failed"):
            DartClient(client, "test-key", interval=0).get("document.xml", {})
    assert len(requests) == 3


def test_zip_validation_respects_uncompressed_size_before_reading_members(monkeypatch):
    import io
    import zipfile

    monkeypatch.setattr(
        "news_preprocessor.sources.publishers.opendart.dart.time.sleep", lambda _: None
    )
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("large.xml", "A" * 1000)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=data.getvalue())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RuntimeError, match="DART request failed"):
            DartClient(client, "test-key", interval=0, max_uncompressed_bytes=100).get(
                "document.xml", {}
            )
    assert len(requests) == 3


@pytest.mark.parametrize("directory_only", [False, True])
def test_zip_without_files_exhausts_three_requests(directory_only, monkeypatch, caplog):
    import io
    import zipfile

    monkeypatch.setattr(
        "news_preprocessor.sources.publishers.opendart.dart.time.sleep", lambda _: None
    )
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        if directory_only:
            archive.writestr("documents/", "")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=data.getvalue())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RuntimeError, match="DART request failed"):
            DartClient(client, "test-key", interval=0).get("document.xml", {})
    assert len(requests) == 3
    final = [r for r in caplog.records if r.message == "dart_request_failed"]
    assert len(final) == 1
    assert final[0].fields["reason"] == "invalid_response"


def test_empty_zip_retries_then_accepts_valid_document(monkeypatch):
    import io
    import zipfile

    monkeypatch.setattr(
        "news_preprocessor.sources.publishers.opendart.dart.time.sleep", lambda _: None
    )
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w"):
        pass
    valid = io.BytesIO()
    with zipfile.ZipFile(valid, "w") as archive:
        archive.writestr("main.xml", "<DOCUMENT><P>disclosure</P></DOCUMENT>")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, content=empty.getvalue() if len(requests) < 3 else valid.getvalue()
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert (
            DartClient(client, "test-key", interval=0).get("document.xml", {}) == valid.getvalue()
        )
    assert len(requests) == 3
