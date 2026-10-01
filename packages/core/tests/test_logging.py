import json
import logging

import pytest
from ktb_core.logging import bind_logger, get_logger, log_run, setup_logging


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


def test_log_run_brackets_a_clean_run(capsys):
    setup_logging("INFO", service_name="svc")
    with log_run(get_logger("svc"), mode="batch") as end:
        end["count"] = 2

    start, finish = _run_events(capsys)
    assert (start["message"], start["mode"]) == ("run_start", "batch")
    assert finish["message"] == "run_end"
    assert (finish["level"], finish["outcome"], finish["exit_code"]) == ("INFO", "ok", 0)
    assert finish["count"] == 2
    assert "elapsed_ms" in finish


@pytest.mark.parametrize(("code", "level", "outcome"), [(0, "INFO", "ok"), (1, "ERROR", "error")])
def test_log_run_reports_a_sys_exit(capsys, code, level, outcome):
    setup_logging("INFO", service_name="svc")
    with pytest.raises(SystemExit), log_run(get_logger("svc")):
        raise SystemExit(code)

    finish = _run_events(capsys)[-1]
    assert (finish["level"], finish["outcome"], finish["exit_code"]) == (level, outcome, code)


def test_log_run_reports_an_exception_over_fields_already_set(capsys):
    setup_logging("INFO", service_name="svc")
    with pytest.raises(ValueError), log_run(get_logger("svc")) as end:
        end["outcome"] = "saved"
        raise ValueError("boom")

    finish = _run_events(capsys)[-1]
    assert (finish["level"], finish["outcome"]) == ("ERROR", "error")
    assert finish["error"] == "ValueError: boom"
    assert "ValueError: boom" in finish["exception"]
