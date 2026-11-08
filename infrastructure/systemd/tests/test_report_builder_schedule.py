from pathlib import Path

UNITS = Path(__file__).resolve().parents[1]
REPO_ROOT = UNITS.parents[1]


def test_report_builder_runs_as_an_always_on_systemd_service():
    unit = (UNITS / "ktb-report-builder.service.in").read_text()

    assert "Type=simple" in unit
    assert "Restart=on-failure" in unit
    assert "RestartMode=direct" in unit
    assert "run --rm --no-deps -T report-builder" in unit
    assert "ktb-scheduled-job-failure@report-builder.service" in unit
    assert "[Install]" in unit
    assert "WantedBy=multi-user.target" in unit
    assert not (UNITS / "ktb-report-builder.timer").exists()


def test_cd_workflows_enable_and_refresh_report_builder():
    for workflow in ["cd.yaml", "cd-dev.yaml"]:
        text = (REPO_ROOT / ".github/workflows" / workflow).read_text()

        assert "systemctl enable ktb-report-builder.service" in text
        stop = text.index("systemctl stop ktb-report-builder.service")
        cleanup = text.index("ps -a -q report-builder")
        reset = text.index("systemctl reset-failed ktb-report-builder.service")
        start = text.index("systemctl start ktb-report-builder.service")
        assert stop < cleanup < reset < start
        assert "systemctl restart ktb-report-builder.service" not in text
        assert "systemctl is-enabled --quiet ktb-report-builder.service" in text
        assert "systemctl is-active --quiet ktb-report-builder.service" in text


def test_compose_prod_leaves_restart_policy_to_systemd():
    compose = (REPO_ROOT / "compose.prod.yaml").read_text()
    report_builder_block = compose.split("report-builder:", 1)[1].split("\n\n", 1)[0]

    assert "restart:" not in report_builder_block
