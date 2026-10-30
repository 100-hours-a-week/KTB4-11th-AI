import io
import zipfile

import pytest
from news_preprocessor.sources.publishers.opendart.disclosures.documents import extract_documents


def archive(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return out.getvalue()


def test_sections_tables_and_all_documents_survive():
    data = archive(
        {
            "main.xml": (
                "<DOCUMENT><TITLE>II. 사업의 내용</TITLE><P>반도체 "
                "매출</P><TITLE>위험관리</TITLE><P>환율 "
                "위험</P><TABLE><TR><TH>구분</TH><TH>금액(억원)</TH></TR><TR><TD>매출</"
                "TD><TD>100</TD></TR></TABLE></DOCUMENT>"
            ),
            "notes.html": "<h2>소송</h2><p>손해배상 소송 진행</p>",
            "attachment.pdf": b"%PDF",
        }
    )
    docs = extract_documents(data)
    assert len(docs) == 3
    assert docs[0]["sections"][0]["section"] == "II. 사업의 내용"
    assert "반도체 매출" in docs[0]["body"]
    assert "구분 | 금액(억원)" in docs[0]["body"]
    assert "매출 | 100" in docs[0]["body"]
    assert docs[1]["sections"][0]["topic"] == "litigation"
    assert docs[2]["status"] == "unsupported"
    for section in docs[0]["sections"]:
        assert docs[0]["body"][section["start"] : section["end"]] == section["text"]


def test_unsafe_archive_is_rejected():
    with pytest.raises(ValueError, match="unsafe"):
        extract_documents(archive({"../escape.xml": "<P>bad</P>"}))


def test_missing_document_has_no_fake_body():
    assert extract_documents(b"<result><status>014</status></result>") == []
    with pytest.raises(ValueError, match="020"):
        extract_documents(b"<result><status>020</status></result>")


def test_table_captions_and_nested_cells_survive():
    docs = extract_documents(
        archive(
            {
                "main.html": (
                    "<h2>매출</h2><table><caption>단위: USD</caption><tr><td colspan="
                    '"2">제품</td></tr><tr><td>외부<table><tr><th>지역</th><th>금액</th><'
                    "/tr><tr><td>한국</td><td>100</td></tr></table></td></tr></tabl"
                    "e>"
                )
            }
        )
    )
    body = docs[0]["body"]
    assert "단위: USD" in body
    assert "지역 | 금액" in body and "한국 | 100" in body
    assert "colspan=2" in body


def test_each_table_has_its_own_retrieval_context():
    docs = extract_documents(
        archive(
            {
                "main.html": (
                    "<h2>재무</h2><table><caption>단위: 억원</caption><tr><th>제품</th><t"
                    "h>매출</th></tr><tr><td>A</td><td>100</td></tr></table><table>"
                    "<caption>단위: 명</caption><tr><th>부서</th><th>인원</th></tr><tr><"
                    "td>B</td><td>20</td></tr></table>"
                )
            }
        )
    )
    sections = docs[0]["sections"]
    assert len(sections) == 2
    assert "제품 | 매출" in sections[0]["context"]
    assert "부서 | 인원" in sections[1]["context"]
    assert "제품 | 매출" not in sections[1]["context"]
