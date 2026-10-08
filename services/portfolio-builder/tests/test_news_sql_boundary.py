import re
from pathlib import Path


def test_portfolio_builder_source_does_not_query_news_tables():
    source = Path(__file__).parents[1] / "src"
    news_tables = (
        "articles|article_clusters|clusters|cluster_summaries|entities|cluster_entities|relations"
    )
    sql_table = re.compile(
        rf"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:public\.)?(?:{news_tables})\b",
        re.IGNORECASE,
    )
    violations = [
        f"{path.relative_to(source)}: {match.group(0)}"
        for path in source.rglob("*.py")
        for match in sql_table.finditer(path.read_text())
    ]

    assert violations == []
