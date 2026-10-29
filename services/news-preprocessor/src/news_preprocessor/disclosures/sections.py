from typing import Any


def section_rows(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten parsed sections into rows, keeping each document's own offsets and order.

    `start_offset` and `end_offset` restart at zero for every file inside a receipt's
    archive, so they are only interpretable alongside `document_name`.
    """
    rows = []
    for document_order, document in enumerate(documents):
        for ordinal, section in enumerate(document["sections"]):
            rows.append(
                {
                    "document_name": document["name"],
                    "document_order": document_order,
                    "ordinal": ordinal,
                    "heading": section["section"],
                    "topic": section["topic"],
                    "text": section["text"],
                    "start_offset": section["start"],
                    "end_offset": section["end"],
                }
            )
    return rows
