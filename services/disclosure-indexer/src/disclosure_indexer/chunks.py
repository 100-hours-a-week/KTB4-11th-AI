from typing import Any

CHUNK_VERSION = "sections-byte-budget-v1"


def chunk_sections(
    sections: list[dict[str, Any]], max_bytes: int = 2400, overlap_bytes: int = 300
) -> list[dict[str, Any]]:
    if max_bytes < 4 or not 0 <= overlap_bytes < max_bytes:
        raise ValueError("invalid chunk budget")
    chunks = []
    for section in sections:
        text = section["text"]
        context_lines = [line for line in text.splitlines() if "단위" in line][:3]
        first_row = next((line for line in text.splitlines() if " | " in line), "")
        if first_row:
            context_lines.append(first_row)
        context = section.get("context") or "\n".join(context_lines)
        start = 0
        while start < len(text):
            end = start
            size = 0
            while end < len(text) and size + len(text[end].encode("utf-8")) <= max_bytes:
                size += len(text[end].encode("utf-8"))
                end += 1
            if end < len(text):
                boundary = text.rfind("\n", start + (end - start) // 2, end)
                if boundary >= 0:
                    end = boundary + 1
            chunks.append(
                {
                    "section": section["section"],
                    "topic": section["topic"],
                    "document": section["document"],
                    "text": text[start:end],
                    "start": section["start"] + start,
                    "end": section["start"] + end,
                    "context": context,
                }
            )
            if end == len(text):
                break
            next_start = end
            overlap = 0
            while (
                next_start > start + 1
                and overlap + len(text[next_start - 1].encode("utf-8")) <= overlap_bytes
            ):
                next_start -= 1
                overlap += len(text[next_start].encode("utf-8"))
            start = next_start
    return chunks
