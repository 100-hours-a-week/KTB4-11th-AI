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
