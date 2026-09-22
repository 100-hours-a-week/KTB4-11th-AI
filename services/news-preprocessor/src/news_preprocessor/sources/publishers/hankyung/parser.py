from bs4 import BeautifulSoup


def parse_article_body(html: str) -> str:
    body = BeautifulSoup(html, "html.parser").select_one(".article-body")
    return " ".join(body.get_text().split()) if body else ""
