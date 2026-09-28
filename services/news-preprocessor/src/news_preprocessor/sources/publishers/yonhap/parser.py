from bs4 import BeautifulSoup


def parse_article_body(html: str) -> str:
    # Unclassed <p> children are the paragraphs; the rest is the byline, photos, ads and copyright.
    paragraphs = BeautifulSoup(html, "html.parser").select(".story-news.article > p:not([class])")
    return " ".join(" ".join(p.get_text() for p in paragraphs).split())
