from disclosure_indexer.sections import section_rows

DOCUMENTS = [
    {
        "name": "00001.xml",
        "status": "ready",
        "sections": [
            {
                "section": "I. 회사의 개요",
                "topic": "other",
                "text": "개요 본문",
                "start": 0,
                "end": 5,
                "document": "00001.xml",
            },
            {
                "section": "II. 사업의 내용",
                "topic": "business",
                "text": "사업 본문",
                "start": 7,
                "end": 12,
                "document": "00001.xml",
            },
        ],
    },
    {
        "name": "00002.xml",
        "status": "ready",
        "sections": [
            {
                "section": "첨부. 감사보고서",
                "topic": "notes",
                "text": "감사 의견",
                "start": 0,
                "end": 5,
                "document": "00002.xml",
            }
        ],
    },
    {"name": "image.jpg", "status": "unsupported", "sections": []},
]


def test_section_rows_number_offsets_and_ordinals_per_document():
    rows = section_rows(DOCUMENTS)

    first = [row for row in rows if row["document_name"] == "00001.xml"]
    second = [row for row in rows if row["document_name"] == "00002.xml"]
    assert [row["ordinal"] for row in first] == [0, 1]
    assert [row["ordinal"] for row in second] == [0]
    # Offsets restart per document, which is why document_name has to travel with them.
    assert first[0]["start_offset"] == 0 and second[0]["start_offset"] == 0


def test_section_rows_keep_archive_order_and_drop_documents_without_sections():
    rows = section_rows(DOCUMENTS)

    assert [row["document_order"] for row in rows] == [0, 0, 1]
    assert "image.jpg" not in {row["document_name"] for row in rows}


def test_section_rows_carry_the_heading_topic_and_range():
    row = section_rows(DOCUMENTS)[1]

    assert row["heading"] == "II. 사업의 내용"
    assert row["topic"] == "business"
    assert row["text"] == "사업 본문"
    assert (row["start_offset"], row["end_offset"]) == (7, 12)
