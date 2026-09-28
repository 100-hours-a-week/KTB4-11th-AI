import json
import logging

from ktb_core.logging import setup_logging


def test_emits_one_json_object_per_record(capsys):
    setup_logging("INFO", service="svc")
    logging.getLogger("svc").info("started")

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["level"] == "INFO"
    assert payload["service"] == "svc"
    assert payload["logger"] == "svc"
    assert payload["message"] == "started"
    assert "timestamp" in payload


def test_preserves_non_ascii_text(capsys):
    setup_logging("INFO", service="svc")
    logging.getLogger("svc").info("척척개미단")

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["message"] == "척척개미단"


def test_respects_the_configured_level(capsys):
    setup_logging("WARNING", service="svc")
    logging.getLogger("svc").info("should not appear")

    assert capsys.readouterr().out == ""


def test_is_idempotent(capsys):
    setup_logging("INFO", service="svc")
    setup_logging("INFO", service="svc")
    logging.getLogger("svc").info("once")

    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_records_exception_text(capsys):
    setup_logging("INFO", service="svc")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("svc").exception("failed")

    payload = json.loads(capsys.readouterr().out.strip())

    assert "ValueError: boom" in payload["exception"]


def test_merges_structured_fields(capsys):
    setup_logging("INFO", service="svc")
    logging.getLogger("svc").info("run_end", extra={"fields": {"outcome": "saved", "turns": 3}})

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["message"] == "run_end"
    assert payload["outcome"] == "saved"
    assert payload["turns"] == 3


def test_serialises_values_json_cannot(capsys):
    from datetime import UTC, datetime

    setup_logging("INFO", service="svc")
    moment = datetime(2026, 9, 28, tzinfo=UTC)
    logging.getLogger("svc").info("x", extra={"fields": {"at": moment}})

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["at"] == str(moment)
