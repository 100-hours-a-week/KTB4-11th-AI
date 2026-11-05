import importlib

import pytest
import sqlalchemy as sa
from pydantic import TypeAdapter, ValidationError


def test_empty_search_is_a_direct_library_call():
    module = importlib.import_module("disclosure_retrieval.search")
    from disclosure_retrieval.queries import SearchResult
    from disclosure_retrieval.settings import Settings

    engine = sa.create_engine("sqlite://")
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(module, "report_rows", lambda *args: [])
            result = module.search(engine, "005930", module.SearchRequest(query="risk"), Settings())
        assert result == SearchResult(reports=[], latest_evidence=[], historical_evidence=[])
    finally:
        engine.dispose()
    with pytest.raises(ValidationError):
        TypeAdapter(SearchResult).validate_python({})
