import argparse
import csv
import logging
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from ktb_core.logging import setup_logging

from harness.clock import FileClock
from harness.contracts import DecisionSystem, InformationProvider
from harness.docker_agent import DockerAgent
from harness.information import (
    DISCLOSURES,
    FEATURES,
    MARKET,
    NEWS,
    HttpInformationProvider,
    NoInformation,
)
from harness.market_http import MarketHttpPriceSource
from harness.recorder import RunDirectory, dumps
from harness.replay import recompute, replay_run
from harness.runner import run_experiment
from harness.settings import RunnerSettings
from harness.spec import ExperimentSpec
from harness.strategies import BuyAndHold, DecisionModelAdapter, EqualWeight, momentum_choices
from harness.submission import SubmissionDesk, create_submission_app

log = logging.getLogger(__name__)
TABLES = ("decisions", "fills", "rejections", "snapshots")


def information_provider(settings: RunnerSettings, client: httpx.Client) -> InformationProvider:
    if settings.information_variant == "none":
        return NoInformation()
    endpoints = {
        MARKET: settings.market_url,
        NEWS: settings.news_url,
        FEATURES: settings.features_url,
        DISCLOSURES: settings.disclosures_url,
    }
    return HttpInformationProvider(
        settings.information_variant,
        {capability: url for capability, url in endpoints.items() if url},
        client,
        settings.information_version,
    )


def decision_system(
    settings: RunnerSettings, desk: SubmissionDesk, client: httpx.Client
) -> DecisionSystem:
    match settings.decision_system:
        case "equal-weight":
            return EqualWeight()
        case "buy-and-hold":
            return BuyAndHold()
        case "jev-stub":
            return DecisionModelAdapter(momentum_choices, name="jev-stub", version="1")
        case "docker-agent":
            assert settings.agent_worker_url and settings.submission_url
            return DockerAgent(
                httpx.Client(
                    base_url=settings.agent_worker_url, timeout=settings.http_timeout_seconds
                ),
                desk,
                settings.submission_url,
                settings.decision_timeout_seconds,
                client,
            )


@contextmanager
def submission_server(settings: RunnerSettings, desk: SubmissionDesk) -> Iterator[None]:
    if settings.decision_system != "docker-agent" or settings.submission_url is None:
        yield
        return
    server = uvicorn.Server(
        uvicorn.Config(
            create_submission_app(desk, settings.submission_url),
            host=settings.submission_host,
            port=settings.submission_port,
            log_config=None,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("submission server did not start")
        time.sleep(0.05)
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def run(_: argparse.Namespace) -> int:
    settings = RunnerSettings()
    setup_logging(settings.log_level, service_name="harness")
    spec = ExperimentSpec.model_validate_json(Path(settings.spec_file).read_text())
    run_id = settings.run_id or f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    run_dir = Path(settings.results_dir) / spec.experiment_id / run_id
    desk = SubmissionDesk()
    timeout = settings.http_timeout_seconds
    with (
        httpx.Client(timeout=timeout) as client,
        httpx.Client(base_url=settings.prices_url, timeout=timeout) as prices,
        submission_server(settings, desk),
    ):
        outcome = run_experiment(
            spec,
            decision_system(settings, desk, client),
            information_provider(settings, client),
            MarketHttpPriceSource(prices),
            FileClock(settings.clock_file),
            run_dir,
            {"code_commit": settings.code_commit, "image_digest": settings.image_digest},
        )
    log.info("run %s finished: %s", outcome.path, outcome.status)
    return outcome.exit_code


def replay(args: argparse.Namespace) -> int:
    with httpx.Client(base_url=args.prices_url, timeout=30) as client:
        report = replay_run(Path(args.run_dir), MarketHttpPriceSource(client))
    print(dumps(report))
    return 0 if not any(report.values()) else 1


def evaluate(args: argparse.Namespace) -> int:
    print(dumps(recompute(Path(args.run_dir))))
    return 0


def export(args: argparse.Namespace) -> int:
    rows = []
    for episode_id, episode in RunDirectory(Path(args.run_dir)).episodes().items():
        for item in getattr(episode, args.table):
            row = asdict(item)
            row.pop("audit", None)
            rows.append({"episode_id": episode_id, **{k: _cell(v) for k, v in row.items()}})
    if rows:
        writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return 0


def _cell(value: object) -> object:
    return dumps(value).replace("\n", "") if isinstance(value, dict | list | tuple) else value


def main() -> None:
    parser = argparse.ArgumentParser(prog="harness")
    commands = parser.add_subparsers(required=True)
    commands.add_parser("run", help="run the experiment configured by HARNESS_*").set_defaults(
        handler=run
    )
    replay_parser = commands.add_parser("replay", help="re-simulate stored decisions")
    replay_parser.add_argument("run_dir")
    replay_parser.add_argument("--prices-url", required=True)
    replay_parser.set_defaults(handler=replay)
    evaluate_parser = commands.add_parser("evaluate", help="recompute metrics from records")
    evaluate_parser.add_argument("run_dir")
    evaluate_parser.set_defaults(handler=evaluate)
    export_parser = commands.add_parser("export", help="print one record table as CSV")
    export_parser.add_argument("run_dir")
    export_parser.add_argument("table", choices=TABLES)
    export_parser.set_defaults(handler=export)
    args = parser.parse_args()
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()
