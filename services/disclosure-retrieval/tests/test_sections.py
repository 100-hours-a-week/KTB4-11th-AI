import pytest
import sqlalchemy as sa
from disclosure_retrieval.sections import read_sections


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
