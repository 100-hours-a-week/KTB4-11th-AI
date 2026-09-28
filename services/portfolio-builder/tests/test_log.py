import json
import logging

from ktb_core.logging import setup_logging
from portfolio_builder.log import make_log


def test_log_emits_event_with_run_id_and_fields(capsys):
    setup_logging("DEBUG", service="svc")
    log = make_log("run-1")

    log("tool_call", logging.WARNING, name="search_graph", is_error=True)

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["message"] == "tool_call"
    assert payload["level"] == "WARNING"
    assert payload["run_id"] == "run-1"
    assert payload["name"] == "search_graph"
    assert payload["is_error"] is True
