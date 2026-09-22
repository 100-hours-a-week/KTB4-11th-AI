from news_preprocessor.sources import ArticleBodyParser, parse_article_text


def _parse(html: str, body_class: str = "body") -> str:
    return parse_article_text(html.encode(), ArticleBodyParser(body_class))


def test_collects_only_the_body_and_collapses_whitespace():
    html = '<p>메뉴</p><div class="body">\n  첫 문장.\n\n  <b>둘째</b> 문장.  </div><p>푸터</p>'

    assert _parse(html) == "첫 문장. 둘째 문장."


def test_a_void_element_inside_the_body_does_not_extend_capture_past_its_end():
    html = (
        '<div class="body">본문<img src="a.jpg"><input type="hidden"></div>'
        "<footer>사이트맵</footer>"
    )

    assert _parse(html) == "본문"


def test_a_self_closing_br_does_not_end_capture_early():
    html = '<div class="body">앞 문장<br/>뒤 문장<br>끝</div>'

    assert _parse(html) == "앞 문장뒤 문장끝"


def test_only_the_first_matching_element_is_captured():
    html = '<div class="body">기사</div><div class="body">추천 기사</div>'

    assert _parse(html) == "기사"


def test_matches_a_whole_class_token_only():
    html = '<div class="body-wrap">광고</div><div class="main body">기사</div>'

    assert _parse(html) == "기사"


def test_returns_empty_text_when_the_class_is_absent():
    assert _parse('<div class="other">기사</div>') == ""


def test_decodes_invalid_utf8_with_replacement():
    parser = ArticleBodyParser("body")

    assert parse_article_text(b'<div class="body">\xff</div>', parser) == "�"
