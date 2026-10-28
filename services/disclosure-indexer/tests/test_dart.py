from datetime import date

import httpx
import pytest
from disclosure_indexer.dart import DartClient, report_period


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


def test_list_reads_all_pages_and_only_requested_company():
    def handle(req):
        assert req.url.params["pblntf_ty"] == "A"
        page = req.url.params["page_no"]
        return httpx.Response(
            200,
            json={
                "status": "000",
                "total_page": 2,
                "list": [
                    {
                        "corp_code": "00126380",
                        "stock_code": "005930",
                        "report_nm": "분기보고서 (2025.03)",
                        "rcept_no": page,
                        "rcept_dt": "20250515",
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        dart = DartClient(client, "test-key", interval=0)
        assert len(dart.reports("00126380", date(2025, 1, 1), date(2026, 1, 1))) == 2


def test_api_error_does_not_expose_key():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"status": "020", "message": "key test-key"})
        )
    ) as client:
        with pytest.raises(RuntimeError) as error:
            DartClient(client, "test-key", interval=0, attempts=1).reports(
                "00126380", date(2025, 1, 1), date(2026, 1, 1)
            )
        assert "test-key" not in str(error.value)
