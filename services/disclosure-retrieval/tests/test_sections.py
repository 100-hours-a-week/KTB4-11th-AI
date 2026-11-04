from datetime import date

import pytest
import sqlalchemy as sa
from disclosure_retrieval.sections import read_sections
from disclosure_retrieval.tables import disclosure_sections, disclosures, metadata


@pytest.fixture
def sectioned(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporations(stock_code,corp_code,name) VALUES "
                "('005930','0005930','회사') ON CONFLICT DO NOTHING"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO disclosures(receipt_id,stock_code,corp_code,company_name,title,"
                "business_year,kind,period_end,published_date,raw_payload,status) VALUES "
                "('sec-1','005930','0005930','회사','사업보고서',2025,'annual','2025-12-31',"
                "'2026-03-01','{}','ready')"
            )
        )
        document_id = conn.execute(
            sa.text(
                "INSERT INTO disclosure_documents(receipt_id,archive_key,source_hash,"
                "parser_version,documents) VALUES ('sec-1','k','h','sections-v1','[]')"
                " RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO disclosure_sections(document_id,receipt_id,document_name,"
                "document_order,ordinal,heading,topic,text,start_offset,end_offset,"
                "parser_version) VALUES "
                "(:d,'sec-1','00001.xml',0,0,'I. 회사의 개요','other','개요',0,2,'sections-v1'),"
                "(:d,'sec-1','00001.xml',0,1,'II. 사업의 내용','business','반도체',4,7,"
                "'sections-v1'),"
                "(:d,'sec-1','00002.xml',1,0,'첨부. 감사보고서','notes','의견',0,2,'sections-v1')"
            ),
            {"d": document_id},
        )
    return engine


def test_sections_are_readable_with_no_embedding_configuration(sectioned, monkeypatch):
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_BASE_URI", raising=False)
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_MODEL", raising=False)
    with sectioned.connect() as conn:
        rows = read_sections(conn, "005930", "sec-1")
    assert [row.heading for row in rows] == [
        "I. 회사의 개요",
        "II. 사업의 내용",
        "첨부. 감사보고서",
    ]
    first = rows[0]
    assert first.receipt_id == "sec-1" and first.document_name == "00001.xml"
    assert (first.start_offset, first.end_offset) == (0, 2)
    assert first.parser_version == "sections-v1"


def test_sections_require_an_explicit_receipt(sectioned):
    with sectioned.connect() as conn, pytest.raises(TypeError):
        read_sections(conn, "005930")


def test_sections_can_be_narrowed_by_document_and_topic(sectioned):
    with sectioned.connect() as conn:
        rows = read_sections(conn, "005930", "sec-1", document_name="00002.xml")
        assert [row.heading for row in rows] == ["첨부. 감사보고서"]
        rows = read_sections(conn, "005930", "sec-1", topic="business")
        assert [row.heading for row in rows] == ["II. 사업의 내용"]


def test_sections_of_another_company_are_not_returned(sectioned):
    with sectioned.connect() as conn:
        assert read_sections(conn, "000660", "sec-1") == []


def test_a_section_read_makes_no_embedding_call(sectioned, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected embedding")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    with sectioned.connect() as conn:
        assert read_sections(conn, "005930", "sec-1")


@pytest.fixture
def sqlite_sections():
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as conn:
        yield conn
    engine.dispose()


def insert_section(conn, receipt_id, published_date, **options):
    conn.execute(
        disclosures.insert(),
        {
            "receipt_id": receipt_id,
            "stock_code": "005930",
            "title": "report",
            "business_year": 2025,
            "kind": "annual",
            "period_end": date(2025, 12, 31),
            "published_date": published_date,
            "status": "ready",
            **options,
        },
    )
    conn.execute(
        disclosure_sections.insert(),
        {
            "id": conn.scalar(sa.select(sa.func.count()).select_from(disclosure_sections)) + 1,
            "receipt_id": receipt_id,
            "document_name": "main.xml",
            "document_order": 0,
            "ordinal": 0,
            "heading": "risk",
            "topic": "risk",
            "text": receipt_id,
            "start_offset": 0,
            "end_offset": len(receipt_id),
            "parser_version": "v1",
        },
    )


def test_section_visibility_uses_latest_revision_at_publication_cutoff(sqlite_sections):
    conn = sqlite_sections
    insert_section(conn, "original", date(2026, 3, 1), detail_type="A001")
    insert_section(conn, "correction", date(2026, 3, 2), detail_type="A002")
    assert read_sections(conn, "005930", "original", as_of=date(2026, 2, 28)) == []
    assert len(read_sections(conn, "005930", "original", as_of=date(2026, 3, 1))) == 1
    assert read_sections(conn, "005930", "correction", as_of=date(2026, 3, 1)) == []
    assert read_sections(conn, "005930", "original", as_of=date(2026, 3, 2)) == []
    assert len(read_sections(conn, "005930", "correction", as_of=date(2026, 3, 2))) == 1
    assert read_sections(conn, "000660", "correction") == []


@pytest.mark.parametrize("status", ["pending", "failed", "unavailable"])
def test_unusable_section_revision_hides_older_sections(sqlite_sections, status):
    conn = sqlite_sections
    insert_section(conn, "original", date(2026, 3, 1))
    insert_section(conn, "latest", date(2026, 3, 2), status=status)
    assert read_sections(conn, "005930", "original") == []
    assert read_sections(conn, "005930", "latest") == []


@pytest.mark.parametrize(
    "options",
    [
        {"detail_type": "F002"},
        {"period_end": date(2025, 6, 30)},
        {"period_end": None},
    ],
)
def test_nonperiodic_section_families_remain_separate(sqlite_sections, options):
    conn = sqlite_sections
    insert_section(
        conn,
        "original",
        date(2026, 3, 1),
        kind="audit",
        detail_type="F001",
        period_end=None if options.get("period_end", True) is None else date(2025, 12, 31),
    )
    insert_section(
        conn, "other", date(2026, 3, 2), kind="audit", **{"detail_type": "F001", **options}
    )
    assert len(read_sections(conn, "005930", "original")) == 1
    assert len(read_sections(conn, "005930", "other")) == 1


def test_nonperiodic_section_correction_supersedes_same_detail_and_period(sqlite_sections):
    conn = sqlite_sections
    insert_section(conn, "original", date(2026, 3, 1), kind="audit", detail_type="F001")
    insert_section(conn, "correction", date(2026, 3, 2), kind="audit", detail_type="F001")
    assert read_sections(conn, "005930", "original") == []
    assert len(read_sections(conn, "005930", "correction")) == 1


def test_sections_default_cutoff_excludes_future_filings(sqlite_sections):
    from datetime import datetime
    from unittest.mock import patch

    conn = sqlite_sections
    insert_section(conn, "original", date(2026, 3, 1))
    insert_section(conn, "future", date(2026, 3, 2))
    with patch("disclosure_retrieval.revisions.datetime") as clock:
        clock.now.return_value = datetime(2026, 3, 1, 23, 59)
        assert len(read_sections(conn, "005930", "original")) == 1
        assert read_sections(conn, "005930", "future") == []


def test_nonperiodic_revision_family_does_not_depend_on_optional_business_year(sqlite_sections):
    conn = sqlite_sections
    insert_section(
        conn, "original", date(2026, 3, 1), kind="audit", detail_type="F001", business_year=None
    )
    insert_section(conn, "correction", date(2026, 3, 2), kind="audit", detail_type="F001")
    assert read_sections(conn, "005930", "original") == []
    assert len(read_sections(conn, "005930", "correction")) == 1
