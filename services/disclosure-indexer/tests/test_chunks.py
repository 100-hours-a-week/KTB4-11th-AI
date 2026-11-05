from disclosure_indexer.chunks import chunk_sections


def test_topic_does_not_change_model_or_drop_other_sections():
    sections = [
        {
            "section": title,
            "topic": topic,
            "text": text,
            "start": 0,
            "end": len(text),
            "document": "main.xml",
        }
        for title, topic, text in [
            ("사업", "business", "제품 매출"),
            ("전망", "outlook", "신규 사업 전망"),
            ("기타", "other", "분류되지 않은 중요한 설명"),
        ]
    ]
    chunks = chunk_sections(sections, max_bytes=60, overlap_bytes=6)
    assert [c["topic"] for c in chunks] == ["business", "outlook", "other"]
    assert chunks[2]["text"] == "분류되지 않은 중요한 설명"


def test_utf8_chunks_preserve_text_and_respect_budget():
    text = "매출 및 소송 위험 " * 100
    chunks = chunk_sections(
        [
            {
                "section": "위험",
                "topic": "risk",
                "text": text,
                "start": 10,
                "end": 10 + len(text),
                "document": "main.xml",
            }
        ],
        max_bytes=120,
        overlap_bytes=15,
    )
    assert len(chunks) > 1
    assert all(len(c["text"].encode()) <= 120 for c in chunks)
    covered = set()
    for c in chunks:
        assert c["text"] == text[c["start"] - 10 : c["end"] - 10]
        covered.update(range(c["start"] - 10, c["end"] - 10))
    assert covered == set(range(len(text)))


def test_split_table_keeps_headers_and_units_in_embedding_context():
    text = "단위: 억원\n구분 | 매출\n" + "반도체 | 100\n" * 50
    chunks = chunk_sections(
        [
            {
                "section": "매출",
                "topic": "business",
                "text": text,
                "start": 0,
                "end": len(text),
                "document": "main.xml",
            }
        ],
        max_bytes=120,
        overlap_bytes=10,
    )
    assert len(chunks) > 1
    assert all("단위: 억원" in c["context"] and "구분 | 매출" in c["context"] for c in chunks)
