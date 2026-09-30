import traceback

import pandas as pd
import pytest
from market_syncer import dart
from market_syncer.dart import DartCorporation, fetch_corp_codes


def reader_returning(frame: pd.DataFrame):
    class FakeReader:
        def __init__(self, api_key: str) -> None:
            self.corp_codes = frame

    return FakeReader


def reader_raising(error_for_key):
    def fake(api_key: str):
        raise error_for_key(api_key)

    return fake


def test_the_real_reader_is_imported():
    from opendartreader import OpenDartReader

    assert dart.OpenDartReader is OpenDartReader


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
    monkeypatch.setattr(dart, "OpenDartReader", reader_returning(frame))

    assert fetch_corp_codes("key") == [
        DartCorporation("00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930"),
        DartCorporation("00164779", "SK하이닉스", None, "000660"),
        DartCorporation("00266961", "NAVER", None, "035420"),
    ]


def test_errors_never_carry_the_key(monkeypatch):
    monkeypatch.setattr(
        dart,
        "OpenDartReader",
        reader_raising(
            lambda key: OSError(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}")
        ),
    )

    key = "SECRET"
    with pytest.raises(RuntimeError) as info:
        fetch_corp_codes(key)

    assert "SECRET" not in "".join(traceback.format_exception(info.value))
    assert info.value.__suppress_context__ is True


def test_dart_status_errors_keep_their_message(monkeypatch):
    monkeypatch.setattr(
        dart,
        "OpenDartReader",
        reader_raising(lambda key: ValueError({"status": "020", "message": "요청 제한을 초과"})),
    )

    with pytest.raises(RuntimeError, match="020"):
        fetch_corp_codes("key")
