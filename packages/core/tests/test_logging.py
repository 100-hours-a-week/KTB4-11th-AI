import json
import logging

import pytest
from ktb_core.logging import bind_logger, get_logger, setup_logging, start_logging


def test_emits_one_json_object_per_record(capsys):
    setup_logging("INFO", service_name="svc")
    logging.getLogger("svc").info("started")

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["level"] == "INFO"
    assert payload["service_name"] == "svc"
    assert payload["logger"] == "svc"
    assert payload["message"] == "started"
    assert "timestamp" in payload


def test_preserves_non_ascii_text(capsys):
    setup_logging("INFO", service_name="svc")
    logging.getLogger("svc").info("척척개미단")

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["message"] == "척척개미단"


def test_respects_the_configured_level(capsys):
    setup_logging("WARNING", service_name="svc")
    logging.getLogger("svc").info("should not appear")

    assert capsys.readouterr().out == ""


def test_is_idempotent(capsys):
    setup_logging("INFO", service_name="svc")
    setup_logging("INFO", service_name="svc")
    logging.getLogger("svc").info("once")

    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_records_exception_text(capsys):
    setup_logging("INFO", service_name="svc")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("svc").exception("failed")

    payload = json.loads(capsys.readouterr().out.strip())

    assert "ValueError: boom" in payload["exception"]


def test_merges_structured_fields(capsys):
    setup_logging("INFO", service_name="svc")
    logging.getLogger("svc").info("run_end", extra={"fields": {"outcome": "saved", "turns": 3}})

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["message"] == "run_end"
    assert payload["outcome"] == "saved"
    assert payload["turns"] == 3


def test_serialises_values_json_cannot(capsys):
    from datetime import UTC, datetime

    setup_logging("INFO", service_name="svc")
    moment = datetime(2026, 9, 28, tzinfo=UTC)
    logging.getLogger("svc").info("x", extra={"fields": {"at": moment}})

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["at"] == str(moment)


def test_bind_logger_puts_bound_fields_on_every_line(capsys):
    setup_logging("DEBUG", service_name="svc")
    log = bind_logger(logging.getLogger("svc"), run_id="run-1")

    log("tool_call", logging.WARNING, name="search_graph", is_error=True)

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["message"] == "tool_call"
    assert payload["level"] == "WARNING"
    assert payload["run_id"] == "run-1"
    assert payload["name"] == "search_graph"
    assert payload["is_error"] is True


def test_get_logger_binds_fields_and_preserves_exception(capsys):
    setup_logging("INFO", service_name="svc")
    log = get_logger("svc").bind(run_id="run-1")

    try:
        raise ValueError("bad")
    except ValueError:
        log.exception("sync_failed", corp_code="42")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["message"] == "sync_failed"
    assert payload["level"] == "ERROR"
    assert payload["run_id"] == "run-1"
    assert payload["corp_code"] == "42"
    assert "ValueError: bad" in payload["exception"]


def _run_events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_emit_run_logs_brackets_a_clean_run(capsys):
    setup_logging("INFO", service_name="svc")
    with start_logging(get_logger("svc"), mode="batch") as end:
        end["count"] = 2

    start, finish = _run_events(capsys)
    assert (start["message"], start["mode"]) == ("run_start", "batch")
    assert finish["message"] == "run_end"
    assert (finish["level"], finish["outcome"], finish["exit_code"]) == ("INFO", "ok", 0)
    assert finish["count"] == 2
    assert "elapsed_ms" in finish


@pytest.mark.parametrize(("code", "level", "outcome"), [(0, "INFO", "ok"), (1, "ERROR", "error")])
def test_emit_run_logs_reports_a_sys_exit(capsys, code, level, outcome):
    setup_logging("INFO", service_name="svc")
    with pytest.raises(SystemExit), start_logging(get_logger("svc")):
        raise SystemExit(code)

    finish = _run_events(capsys)[-1]
    assert (finish["level"], finish["outcome"], finish["exit_code"]) == (level, outcome, code)


def test_emit_run_logs_reports_an_exception_over_fields_already_set(capsys):
    setup_logging("INFO", service_name="svc")
    with pytest.raises(ValueError), start_logging(get_logger("svc")) as end:
        end["outcome"] = "saved"
        raise ValueError("boom")

    finish = _run_events(capsys)[-1]
    assert (finish["level"], finish["outcome"]) == ("ERROR", "error")
    assert finish["error"] == "ValueError: boom"
    assert "ValueError: boom" in finish["exception"]


KEY = "0123456789abcdef0123456789abcdef01234567"
DART_URL = f"https://opendart.fss.or.kr/api/list.json?crtfc_key={KEY}&rcept_no=20260928000386"
MASKED_URL = "https://opendart.fss.or.kr/api/list.json?crtfc_key=***&rcept_no=20260928000386"


def _masked_lines(capsys):
    out = capsys.readouterr().out
    assert KEY not in out
    return [json.loads(line) for line in out.splitlines()]


def test_masks_sensitive_query_params_in_message_fields_and_exception(capsys):
    setup_logging("INFO", service_name="svc", sensitive_query_params={"crtfc_key"})
    logging.getLogger("httpx").info('HTTP Request: GET %s "HTTP/1.1 200 OK"', DART_URL)
    get_logger("svc").warning("feed_failed", url=DART_URL, nested={"urls": [DART_URL]})
    try:
        raise RuntimeError(f"Client error for url '{DART_URL}'")
    except RuntimeError:
        get_logger("svc").exception("article_failed")

    request, field, failure = _masked_lines(capsys)

    assert request["message"] == f'HTTP Request: GET {MASKED_URL} "HTTP/1.1 200 OK"'
    assert field["url"] == MASKED_URL
    assert field["nested"] == {"urls": [MASKED_URL]}
    assert f"Client error for url '{MASKED_URL}'" in failure["exception"]


def test_a_masked_value_before_a_quote_or_newline_keeps_the_json_valid(capsys):
    setup_logging("INFO", service_name="svc", sensitive_query_params={"crtfc_key"})
    url = f"https://opendart.fss.or.kr/api/list.json?page_no=1&crtfc_key={KEY}"
    logging.getLogger("svc").info('GET "%s"', url)
    logging.getLogger("svc").info("%s\nnext line", url)

    quoted, newline = _masked_lines(capsys)

    masked = "https://opendart.fss.or.kr/api/list.json?page_no=1&crtfc_key=***"
    assert quoted["message"] == f'GET "{masked}"'
    assert newline["message"] == f"{masked}\nnext line"


def test_query_params_are_left_alone_by_default(capsys):
    setup_logging("INFO", service_name="svc")
    logging.getLogger("svc").info("crtfc_key=abc")

    assert json.loads(capsys.readouterr().out)["message"] == "crtfc_key=abc"
