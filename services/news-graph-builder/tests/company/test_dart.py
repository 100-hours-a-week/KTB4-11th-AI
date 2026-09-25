import traceback

import pandas as pd
import pytest
from news_graph_builder.company import DartCompany, dart, fetch_corp_codes


def test_keeps_listed_companies(monkeypatch):
    frame = pd.DataFrame(
        [
            ["00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930", "20251201"],
            ["00999999", "비상장", "Unlisted", " ", "20250101"],
            ["00164779", "SK하이닉스", " ", "000660", "20240328"],
            ["00266961", "NAVER", None, "035420", "20240311"],
            ["00888888", "상장폐지", "Delisted", None, "20240101"],
        ],
        columns=["corp_code", "corp_name", "corp_eng_name", "stock_code", "modify_date"],
    )
    monkeypatch.setattr(dart, "corp_codes", lambda api_key: frame)

    assert fetch_corp_codes("key") == [
        DartCompany("00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930"),
        DartCompany("00164779", "SK하이닉스", None, "000660"),
        DartCompany("00266961", "NAVER", None, "035420"),
    ]


def test_errors_never_carry_the_key(monkeypatch):
    def failing(api_key):
        raise OSError(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={api_key}")

    monkeypatch.setattr(dart, "corp_codes", failing)

    key = "SECRET"
    with pytest.raises(RuntimeError) as info:
        fetch_corp_codes(key)

    assert "SECRET" not in "".join(traceback.format_exception(info.value))
    assert info.value.__suppress_context__ is True


def test_dart_status_errors_keep_their_message(monkeypatch):
    def refusing(api_key):
        raise ValueError({"status": "020", "message": "요청 제한을 초과하였습니다."})

    monkeypatch.setattr(dart, "corp_codes", refusing)

    with pytest.raises(RuntimeError, match="020"):
        fetch_corp_codes("key")
