import io
import zipfile
from pathlib import PurePosixPath
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag


def parse_document_body(document: bytes) -> str:
    # KRX filings declare charset=euc-kr over UTF-8 bytes; a str makes the parser ignore the tag.
    text = document.decode("utf-8")
    parser = "xml" if text.lstrip().startswith("<?xml") else "html.parser"
    soup = BeautifulSoup(text, parser)
    for element in soup.select("style, script"):
        element.decompose()
    return " ".join(soup.get_text(" ").split())


PARSER_VERSION = "sections-v2-content-kind"


def extract_documents(
    content: bytes, max_uncompressed_bytes: int = 200_000_000
) -> list[dict[str, Any]]:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        status = BeautifulSoup(content, "xml").find("status")
        code = status.get_text(strip=True) if status else "unknown"
        if code == "014":
            return []
        raise ValueError(f"DART document status {code}")
    documents = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if sum(info.file_size for info in archive.infolist()) > max_uncompressed_bytes:
            raise ValueError("archive exceeds uncompressed limit")
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                raise ValueError("unsafe archive member")
            if path.suffix.lower() not in (".xml", ".html", ".htm"):
                documents.append(
                    {"name": info.filename, "status": "unsupported", "body": "", "sections": []}
                )
                continue
            raw = archive.read(info)
            documents.append(parse_document(raw, info.filename))
    return documents


def parse_document(raw: bytes, filename: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")
    soup = BeautifulSoup(
        text,
        "xml" if text.lstrip().startswith("<?xml") or "<DOCUMENT" in text[:1000] else "html.parser",
    )
    for item in soup.find_all(lambda tag: tag.name.lower() in ("script", "style")):
        item.decompose()
    sections = []
    title = "본문"
    topic = "other"
    parts = []
    offset = 0
    buffer = []
    headings = {"title", "h1", "h2", "h3", "h4", "h5", "h6"}
    paragraphs = {"p", "paragraph", "li", "caption"}

    def flush(context: str = "", content_kind: str = "narrative"):
        nonlocal offset
        value = "\n".join(buffer).strip()
        buffer.clear()
        if value:
            if parts:
                offset += 2
            parts.append(value)
            sections.append(
                {
                    "section": title,
                    "topic": topic,
                    "text": value,
                    "start": offset,
                    "end": offset + len(value),
                    "document": filename,
                    "context": context,
                    "content_kind": content_kind,
                }
            )
            offset += len(value)

    def walk(node):
        nonlocal title, topic
        if isinstance(node, NavigableString):
            value = " ".join(str(node).split())
            if value:
                buffer.append(value)
            return
        if not isinstance(node, Tag):
            return
        name = node.name.lower()
        if name in headings:
            flush()
            title = " ".join(node.get_text(" ").split()) or "본문"
            topic = "other"
            for label, words in (
                ("litigation", ("소송", "우발")),
                ("outlook", ("전망", "경영진단", "분석의견")),
                ("risk", ("위험", "리스크")),
                ("contracts", ("계약", "수주")),
                ("notes", ("주석",)),
                ("research", ("연구", "개발")),
                ("production", ("원재료", "생산", "설비")),
                ("business", ("사업", "제품", "매출")),
            ):
                if any(word in title for word in words):
                    topic = label
                    break
        elif name == "table":
            preceding = [line for line in buffer if "단위" in line][-3:]
            flush()
            value = parse_table(node)
            if value:
                buffer.append(value)
                captions = [
                    " ".join(c.get_text(" ").split())
                    for c in node.find_all(lambda t: t.name.lower() == "caption")
                ]
                header = next((line for line in value.splitlines() if " | " in line), "")
                flush("\n".join([*preceding, *captions, header]), "table")
        elif name in paragraphs and not node.find(lambda t: t.name.lower() in headings | {"table"}):
            value = " ".join(node.get_text(" ").split())
            if value:
                buffer.append(value)
        else:
            for child in node.children:
                walk(child)

    walk(soup)
    flush()
    body = "\n\n".join(parts)
    return {
        "name": filename,
        "status": "ready" if body else "empty",
        "body": body,
        "sections": sections,
    }


def parse_table(table: Tag) -> str:
    lines = []
    for caption in table.find_all(lambda t: t.name.lower() == "caption", recursive=False):
        lines.append(" ".join(caption.get_text(" ").split()))
    for row in table.find_all(lambda t: t.name.lower() == "tr"):
        if row.find_parent(lambda t: t.name.lower() == "table") is not table:
            continue
        values = []
        nested = []
        for cell in row.find_all(lambda t: t.name.lower() in ("th", "td"), recursive=False):
            words = [
                str(node).strip()
                for node in cell.descendants
                if isinstance(node, NavigableString)
                and node.find_parent(lambda t: t.name.lower() == "table") is table
            ]
            value = " ".join(" ".join(words).split())
            spans = [
                f"{attr}={cell.get(attr)}"
                for attr in ("rowspan", "colspan")
                if cell.get(attr) not in (None, "1")
            ]
            if spans:
                value += " [" + ", ".join(spans) + "]"
            values.append(value)
            for child in cell.find_all(lambda t: t.name.lower() == "table"):
                if child.find_parent(lambda t: t.name.lower() == "table") is table:
                    nested.append(parse_table(child))
        if values:
            lines.append(" | ".join(values))
        lines.extend(nested)
    return "\n".join(lines)


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
