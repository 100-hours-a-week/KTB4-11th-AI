import pytest
from news_graph_builder.normalize import normalize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("삼성전자", "삼성전자"),
        ("삼성전자(주)", "삼성전자"),
        ("(주) 삼성전자", "삼성전자"),
        ("㈜LG화학", "lg화학"),
        ("주식회사 카카오", "카카오"),
        (" SK 하이닉스\t", "sk하이닉스"),
        ("SK hynix Inc.", "skhynixinc."),
        ("(주)", ""),
    ],
)
def test_normalize(text, expected):
    assert normalize(text) == expected
