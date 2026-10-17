from bs4 import BeautifulSoup


def parse_document_body(document: bytes) -> str:
    # KRX filings declare charset=euc-kr over UTF-8 bytes; a str makes the parser ignore the tag.
    text = document.decode("utf-8")
    parser = "xml" if text.lstrip().startswith("<?xml") else "html.parser"
    soup = BeautifulSoup(text, parser)
    for element in soup.select("style, script"):
        element.decompose()
    return " ".join(soup.get_text(" ").split())
