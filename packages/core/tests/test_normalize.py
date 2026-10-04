import pytest
from ktb_core.normalize import normalize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("삼성전자", "삼성전자"),
        ("삼성전자(주)", "삼성전자"),
        ("(주) 삼성전자", "삼성전자"),
        ("㈜LG화학", "lg화학"),
        ("㈜ 삼성 전자", "삼성전자"),
        ("주식회사 카카오", "카카오"),
        ("삼성전자 주식회사", "삼성전자"),
        (" SK 하이닉스\t", "sk하이닉스"),
        ("SK hynix Inc.", "skhynixinc."),
        ("LG Energy Solution", "lgenergysolution"),
        ("(주)", ""),
        ("（주）삼성전자", "삼성전자"),
        ("ＬＧ화학", "lg화학"),
    ],
)
def test_normalize(text, expected):
    assert normalize(text) == expected
