import json
import pathlib

import pytest
from news_graph_builder.common import normalize

REPO_ROOT = next(
    parent
    for parent in pathlib.Path(__file__).resolve().parents
    if (parent / "alembic.ini").is_file()
)
# portfolio-builder normalizes names the same way to look up company_aliases.
CASES = json.loads(
    (REPO_ROOT / "services/portfolio-builder/tests/fixtures/normalize_cases.json").read_text()
)


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_matches_the_shared_cases(raw, expected):
    assert normalize(raw) == expected
