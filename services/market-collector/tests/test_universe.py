import pytest
from market_collector.universe import load_from, load_kospi200


@pytest.mark.skip(reason="awaiting the KRX KOSPI 200 export")
def test_the_shipped_list_has_two_hundred_six_digit_codes():
    codes = load_kospi200()

    assert len(codes) == 200
    assert all(len(c) == 6 and c.isdigit() for c in codes)


def test_load_from_reads_a_two_column_csv(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("code,name\n005930,삼성전자\n000660,SK하이닉스\n", encoding="utf-8")

    assert load_from(path) == frozenset({"005930", "000660"})


def test_a_malformed_code_is_rejected(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("code,name\n5930,삼성전자\n", encoding="utf-8")

    with pytest.raises(ValueError, match="5930"):
        load_from(path)


def test_a_duplicate_code_is_rejected(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("code,name\n005930,삼성전자\n005930,삼성전자\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_from(path)
