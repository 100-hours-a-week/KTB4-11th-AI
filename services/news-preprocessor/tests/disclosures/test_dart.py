from datetime import date

import httpx
import pytest
from news_preprocessor.sources.publishers.opendart.disclosures.dart import DartClient, report_period


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
