import json
import logging

from ktb_core.logging import setup_logging


def test_emits_one_json_object_per_record(capsys):
    setup_logging("INFO")
    logging.getLogger("svc").info("started")

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["level"] == "INFO"
    assert payload["logger"] == "svc"
    assert payload["message"] == "started"
    assert "timestamp" in payload


def test_preserves_non_ascii_text(capsys):
    setup_logging("INFO")
    logging.getLogger("svc").info("척척개미단")

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["message"] == "척척개미단"


def test_respects_the_configured_level(capsys):
    setup_logging("WARNING")
    logging.getLogger("svc").info("should not appear")

    assert capsys.readouterr().out == ""


def test_is_idempotent(capsys):
    setup_logging("INFO")
    setup_logging("INFO")
    logging.getLogger("svc").info("once")

    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_records_exception_text(capsys):
    setup_logging("INFO")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("svc").exception("failed")

    payload = json.loads(capsys.readouterr().out.strip())

    assert "ValueError: boom" in payload["exception"]


def test_merges_structured_fields(capsys):
    setup_logging("INFO")
    logging.getLogger("svc").info("run_end", extra={"fields": {"outcome": "saved", "turns": 3}})

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["message"] == "run_end"
    assert payload["outcome"] == "saved"
    assert payload["turns"] == 3


def test_serialises_values_json_cannot(capsys):
    from datetime import UTC, datetime

    setup_logging("INFO")
    moment = datetime(2026, 9, 28, tzinfo=UTC)
    logging.getLogger("svc").info("x", extra={"fields": {"at": moment}})

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["at"] == str(moment)


def test_redacts_secrets_anywhere_in_the_line(capsys):
    setup_logging("INFO")
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
    logging.getLogger("svc").error(
        "failed",
        extra={
            "fields": {
                "error": 'Bearer sk-or-v1-0123abcDEF rejected; body {"access_token": "abc"}',
                "token": jwt,
            }
        },
    )

    line = capsys.readouterr().out
    payload = json.loads(line)

    assert "sk-or-v1-0123abcDEF" not in line
    assert 'abc\\"' not in line
    assert jwt not in line
    assert "[redacted-key]" in payload["error"]
    assert '\\"access_token\\":\\"[redacted]\\"' in line
    assert payload["token"] == "[redacted-jwt]"


def test_redaction_never_breaks_the_json_line(capsys):
    setup_logging("INFO")
    logging.getLogger("svc").error(
        "failed", extra={"fields": {"error": 'body truncated: {"access_token":', "next": "keep"}}
    )

    payload = json.loads(capsys.readouterr().out)

    assert payload["next"] == "keep"
