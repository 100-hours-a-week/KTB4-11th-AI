from bs4 import BeautifulSoup


def parse_article_body(html: bytes) -> str:
    body = BeautifulSoup(html, "html.parser").select_one(".news_cnt_detail_wrap")
    return " ".join(body.get_text().split()) if body else ""
