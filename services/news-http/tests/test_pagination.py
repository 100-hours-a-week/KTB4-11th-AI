from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from news_http.controllers.pagination import decode_cursor, encode_cursor, paginate

KST = timezone(timedelta(hours=9))


def b64(text: str) -> str:
    return urlsafe_b64encode(text.encode()).decode()


def test_cursor_round_trips_offset_and_microseconds():
    at = datetime(2026, 10, 5, 9, 12, 30, 123456, tzinfo=KST)

    assert decode_cursor(encode_cursor(at, 812)) == (at, 812)


@pytest.mark.parametrize(
    "cursor",
    [
        "!!!",
        b64("no separator"),
        b64("2026-10-05T00:00:00+00:00|x"),
        b64("not a date|1"),
        b64("2026-10-05T00:00:00|1"),
        b64("2026-10-05T00:00:00+00:00|1|2"),
        urlsafe_b64encode(b"\xff\xfe|1").decode(),
    ],
)
def test_malformed_cursor_is_a_422(cursor):
    with pytest.raises(HTTPException) as raised:
        decode_cursor(cursor)

    assert raised.value.status_code == 422


def key(row: tuple[datetime, int]) -> tuple[datetime, int]:
    return row


ROWS = [(datetime(2026, 10, day, tzinfo=UTC), day) for day in (3, 2, 1)]


def test_a_short_page_is_the_last():
    page = paginate(ROWS[:2], 2, key)

    assert page.items == ROWS[:2]
    assert page.next_cursor is None


def test_an_extra_row_yields_the_cursor_of_the_last_item():
    page = paginate(ROWS, 2, key)

    assert page.items == ROWS[:2]
    assert page.next_cursor is not None
    assert decode_cursor(page.next_cursor) == ROWS[1]
