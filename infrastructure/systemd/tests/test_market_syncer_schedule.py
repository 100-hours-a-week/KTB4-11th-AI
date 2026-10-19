from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SYSTEMD = ROOT / "infrastructure" / "systemd"
SCRIPTS = ROOT / "scripts"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_market_syncer_runs_independently_before_morning_pipeline() -> None:
    sync_timer = read(SYSTEMD / "ktb-market-syncer.timer")
    morning_timer = read(SYSTEMD / "ktb-morning-pipeline.timer")
    morning_script = read(SCRIPTS / "run-morning-pipeline.sh")

    assert "OnCalendar=Mon..Fri *-*-* 05:55:00 Asia/Seoul" in sync_timer
    assert "Persistent=true" in sync_timer
    assert "OnCalendar=Mon..Fri *-*-* 06:00:00 Asia/Seoul" in morning_timer
    assert "run_job market-syncer" not in morning_script


def test_market_syncer_retries_and_records_success() -> None:
    service = read(SYSTEMD / "ktb-market-syncer.service.in")

    assert "Restart=on-failure" in service
    assert "RestartSec=5m" in service
    assert "StartLimitBurst=3" in service
    assert "ExecStartPost=/usr/bin/touch @@DEPLOY_DIR@@/.market-syncer-success" in service


def test_reference_consumers_require_fresh_sync() -> None:
    morning = read(SYSTEMD / "ktb-morning-pipeline.service.in")
    streamer = read(SYSTEMD / "ktb-market-streamer.service.in")
    collector = read(SYSTEMD / "ktb-market-collector.service.in")

    assert "ExecStartPre=@@DEPLOY_DIR@@/scripts/wait-for-market-syncer.sh 5400" in morning
    assert "ExecStartPre=@@DEPLOY_DIR@@/scripts/wait-for-market-syncer.sh 0" in streamer
    assert "ExecStartPre=@@DEPLOY_DIR@@/scripts/wait-for-market-syncer.sh 0" in collector


def test_both_cd_workflows_install_and_enable_the_sync_schedule() -> None:
    for name in ("cd.yaml", "cd-dev.yaml"):
        workflow = read(ROOT / ".github" / "workflows" / name)
        assert "scripts/wait-for-market-syncer.sh" in workflow
        assert "ktb-market-syncer.timer" in workflow
        assert "systemd-analyze verify" in workflow

        legacy_block = workflow.split("for legacy_timer in", 1)[1].split("done", 1)[0]
        assert "ktb-market-syncer.timer" not in legacy_block


def test_freshness_gate_accepts_today_marker(tmp_path: Path) -> None:
    marker = tmp_path / ".market-syncer-success"
    marker.touch()

    result = subprocess.run(
        ["bash", str(SCRIPTS / "wait-for-market-syncer.sh"), "0"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "freshness check passed" in result.stdout


def test_freshness_gate_rejects_stale_marker(tmp_path: Path) -> None:
    marker = tmp_path / ".market-syncer-success"
    marker.touch()
    os.utime(marker, (1, 1))

    result = subprocess.run(
        ["bash", str(SCRIPTS / "wait-for-market-syncer.sh"), "0"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "no successful run" in result.stderr
