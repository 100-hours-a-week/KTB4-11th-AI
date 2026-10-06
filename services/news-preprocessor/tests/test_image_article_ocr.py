import logging
import os
import pathlib
import shutil
import subprocess
import time
from datetime import datetime, timezone
from unittest.mock import Mock

import httpx
import pytest
import sqlalchemy as sa
from bs4 import BeautifulSoup
from news_preprocessor.sources import EmptyBodyError, FeedEntry
from news_preprocessor.sources.article_body import body_or_title
from news_preprocessor.sources.publishers import (
    ChosunEconomyRSS,
    EdailyRSS,
    HankyungEconomyRSS,
    MaeilBusinessEconomyRSS,
    SeoulEconomicRSS,
    YonhapEconomyRSS,
)

IMAGE = pathlib.Path(__file__).parent / "fixtures" / "image_article.png"
IMAGE_URL = "https://example.com/images/article.png"
ENTRY = FeedEntry(
    source="test",
    external_id="https://example.com/news/1",
    url="https://example.com/news/1",
    title="이미지 기사 제목",
    published_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
    raw_payload="<item />",
)
TEXT = "Company revenue increased by 20 percent this quarter."


def _tsv(text=TEXT, confidence=95):
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tconf\ttext\n"
    return header + "".join(
        f"5\t1\t1\t1\t1\t{index}\t{confidence}\t{word}\n"
        for index, word in enumerate(text.split(), 1)
    )


@pytest.fixture
def ocr_process(monkeypatch):
    process = Mock(return_value=subprocess.CompletedProcess([], 0, _tsv().encode(), b""))
    monkeypatch.setattr(subprocess, "run", process)
    return process


def _client(status=200, content=None):
    def response(request):
        assert str(request.url) == IMAGE_URL
        return httpx.Response(status, content=IMAGE.read_bytes() if content is None else content)

    return httpx.Client(transport=httpx.MockTransport(response))


def test_image_only_body_uses_ocr_text(ocr_process):
    with _client() as client:
        body = body_or_title(ENTRY, "", True, image_urls=[IMAGE_URL], client=client)

    assert body == TEXT
    arguments = ocr_process.call_args.args[0]
    assert arguments[:3] == ["tesseract", "stdin", "stdout"]
    assert "kor+eng" in arguments
    assert arguments[-1] == "tsv"
    assert ocr_process.call_args.kwargs["timeout"] == 15


@pytest.mark.parametrize("body, has_image", [("기존 본문", True), ("", False)])
def test_existing_text_and_title_only_news_do_not_run_ocr(ocr_process, body, has_image):
    assert body_or_title(ENTRY, body, has_image, image_urls=[IMAGE_URL]) == (body or ENTRY.title)
    ocr_process.assert_not_called()


@pytest.mark.parametrize(
    "output",
    [_tsv(""), _tsv("짧음"), _tsv(confidence=10), _tsv("!!!!!!!!!!!!!!!!!!!!!!!!!")],
)
def test_empty_or_low_quality_ocr_preserves_empty_body_skip_policy(ocr_process, caplog, output):
    ocr_process.return_value.stdout = output.encode()
    with _client() as client, caplog.at_level(logging.WARNING), pytest.raises(EmptyBodyError):
        body_or_title(ENTRY, "", True, image_urls=[IMAGE_URL], client=client)

    warning = next(record for record in caplog.records if record.message == "article_ocr_failed")
    assert warning.fields["url"] == ENTRY.url
    assert warning.fields["image_url"] == IMAGE_URL


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("tesseract"), subprocess.TimeoutExpired("tesseract", 15)],
)
def test_ocr_runtime_failure_preserves_warning_and_skip(ocr_process, caplog, error):
    ocr_process.side_effect = error
    with _client() as client, caplog.at_level(logging.WARNING), pytest.raises(EmptyBodyError):
        body_or_title(ENTRY, "", True, image_urls=[IMAGE_URL], client=client)
    assert "article_ocr_failed" in caplog.text


@pytest.mark.parametrize("status, content", [(503, b"error"), (200, b"not an image")])
def test_bad_image_response_does_not_run_ocr(ocr_process, status, content):
    with _client(status, content) as client, pytest.raises(EmptyBodyError):
        body_or_title(ENTRY, "", True, image_urls=[IMAGE_URL], client=client)
    ocr_process.assert_not_called()


@pytest.mark.parametrize("variable, value", [("MAX_IMAGE_BYTES", "10"), ("MAX_IMAGE_PIXELS", "10")])
def test_large_images_are_rejected_before_ocr(ocr_process, monkeypatch, variable, value):
    monkeypatch.setenv(f"NEWS_PREPROCESSOR_OCR_{variable}", value)
    with _client() as client, pytest.raises(EmptyBodyError):
        body_or_title(ENTRY, "", True, image_urls=[IMAGE_URL], client=client)
    ocr_process.assert_not_called()


def test_too_many_images_are_not_partially_processed(ocr_process):
    with _client() as client, pytest.raises(EmptyBodyError):
        body_or_title(ENTRY, "", True, image_urls=[IMAGE_URL] * 4, client=client)
    ocr_process.assert_not_called()


def test_lazy_and_relative_image_urls_are_resolved_and_deduplicated():
    from news_preprocessor.sources.article_body import article_image_urls

    content = BeautifulSoup(
        '<div><img src="data:image/gif;base64,AA" data-src="../images/article.png">'
        '<picture><source srcset="/images/article.png 1x, /images/article.png 2x"></picture>'
        "</div>",
        "html.parser",
    ).div
    assert article_image_urls(content, ENTRY.url) == [IMAGE_URL]


def test_picture_alternatives_are_one_logical_image():
    from news_preprocessor.sources.article_body import article_image_urls

    content = BeautifulSoup(
        '<div><picture><source srcset="/page.avif" type="image/avif">'
        '<source srcset="/page.webp" type="image/webp"><img src="/page.jpg"></picture></div>',
        "html.parser",
    ).div
    assert article_image_urls(content, ENTRY.url) == ["https://example.com/page.jpg"]


def test_picture_uses_source_when_fallback_image_is_a_placeholder():
    from news_preprocessor.sources.article_body import article_image_urls

    content = BeautifulSoup(
        '<div><picture><source srcset="/page.webp">'
        '<img src="data:image/gif;base64,AA"></picture></div>',
        "html.parser",
    ).div
    assert article_image_urls(content, ENTRY.url) == ["https://example.com/page.webp"]


def test_unresolvable_image_does_not_silently_drop_part_of_the_article():
    from news_preprocessor.sources.article_body import article_image_urls

    content = BeautifulSoup(
        '<div><img src="/images/article.png"><img data-unknown-lazy-src="/part2.png"></div>',
        "html.parser",
    ).div
    with pytest.raises(EmptyBodyError):
        article_image_urls(content, ENTRY.url)


def test_total_download_time_is_bounded(ocr_process, monkeypatch):
    clock = iter([0, 11])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    with _client() as client, pytest.raises(EmptyBodyError):
        body_or_title(ENTRY, "", True, image_urls=[IMAGE_URL], client=client)
    ocr_process.assert_not_called()


@pytest.fixture(
    params=[
        (
            ChosunEconomyRSS,
            'Fusion.globalContent={"content_elements":[{"type":"image","url":"/images/article.png"}]}',
        ),
        (EdailyRSS, '<div class="news_body"><img src="/images/article.png"></div>'),
        (HankyungEconomyRSS, '<div class="article-body"><img src="/images/article.png"></div>'),
        (
            MaeilBusinessEconomyRSS,
            '<div class="news_cnt_detail_wrap"><img src="/images/article.png"></div>',
        ),
        (SeoulEconomicRSS, '<div id="article-body"><img src="/images/article.png"></div>'),
        (
            YonhapEconomyRSS,
            '<div class="story-news article"><figure>'
            '<img src="/images/article.png"></figure></div>',
        ),
    ],
    ids=["chosun", "edaily", "hankyung", "maeil", "sedaily", "yonhap"],
)
def image_source(request):
    factory, article_html = request.param

    def response(request):
        if str(request.url) == ENTRY.url:
            return httpx.Response(200, text=article_html)
        assert str(request.url) == IMAGE_URL
        return httpx.Response(200, content=IMAGE.read_bytes())

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        if factory in (EdailyRSS, SeoulEconomicRSS):
            yield factory(client, "economy")
        else:
            yield factory(client)


def test_every_publisher_recovers_image_only_body_and_preserves_metadata(image_source, ocr_process):
    item = image_source.article(ENTRY)

    assert item.body == TEXT
    assert item.title == ENTRY.title
    assert item.url == ENTRY.url
    assert item.published_at == ENTRY.published_at
    assert item.raw_payload == ENTRY.raw_payload


@pytest.mark.parametrize("recognized_text, expected_saved", [(TEXT, True), ("", False)])
def test_scrape_stores_only_successful_ocr(
    image_source, ocr_process, monkeypatch, recognized_text, expected_saved
):
    from news_preprocessor import scrape as scraping

    ocr_process.return_value.stdout = _tsv(recognized_text).encode()
    stored = []
    monkeypatch.setattr(image_source, "entries", lambda: [ENTRY])
    monkeypatch.setattr(scraping, "known_external_ids", lambda conn, source, ids: set())
    monkeypatch.setattr(scraping, "insert_new", lambda conn, item: stored.append(item) or True)
    engine = sa.create_engine("sqlite://")
    try:
        result = scraping.scrape(engine, image_source)
    finally:
        engine.dispose()

    assert result.failed == []
    assert result.succeed == ([ENTRY.external_id] if expected_saved else [])
    assert [item.body for item in stored] == ([TEXT] if expected_saved else [])


def test_partial_ocr_is_not_saved_as_a_complete_body(ocr_process):
    ocr_process.side_effect = [
        subprocess.CompletedProcess([], 0, _tsv().encode(), b""),
        subprocess.TimeoutExpired("tesseract", 15),
    ]
    with _client() as client, pytest.raises(EmptyBodyError):
        body_or_title(ENTRY, "", True, image_urls=[IMAGE_URL, IMAGE_URL], client=client)


def test_real_korean_and_english_ocr_from_image_fixture():
    if shutil.which("tesseract") is None:
        if os.getenv("KTB_TEST_OCR_RUNTIME"):
            pytest.fail("CI OCR runtime is missing")
        pytest.skip("tesseract is not installed")
    languages = subprocess.run(
        ["tesseract", "--list-langs"], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    if "kor" not in languages or "eng" not in languages:
        if os.getenv("KTB_TEST_OCR_RUNTIME"):
            pytest.fail("CI OCR language data is missing")
        pytest.skip("Korean and English OCR language data is not installed")
    image = IMAGE.with_name("image_article_korean.png").read_bytes()
    with _client(content=image) as client:
        text = body_or_title(ENTRY, "", True, image_urls=[IMAGE_URL], client=client)
    assert "기업 매출" in text
    assert "20퍼센트" in "".join(text.split())
    assert "Company revenue" in text


@pytest.mark.parametrize("recognized_text, expected_bodies", [(TEXT, [TEXT]), ("", [])])
def test_postgres_stores_only_successful_ocr(
    engine, image_source, ocr_process, monkeypatch, recognized_text, expected_bodies
):
    from news_preprocessor.scrape import scrape
    from news_preprocessor.storage import articles

    ocr_process.return_value.stdout = _tsv(recognized_text).encode()
    monkeypatch.setattr(image_source, "entries", lambda: [ENTRY])

    result = scrape(engine, image_source)

    assert result.failed == []
    with engine.connect() as connection:
        assert list(connection.execute(sa.select(articles.c.body)).scalars()) == expected_bodies
