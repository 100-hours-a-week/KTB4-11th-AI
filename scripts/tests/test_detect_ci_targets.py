import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "detect_ci_targets", Path(__file__).parents[1] / "detect_ci_targets.py"
)
detect_ci_targets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detect_ci_targets)
detect = detect_ci_targets.detect


def test_package_change_includes_dependent_services():
    full, services, members = detect(["packages/telemetry/src/ktb_telemetry/tracing.py"])

    assert not full
    assert services == ["market-http", "market-streamer"]
    assert members == ["packages/telemetry", "services/market-http", "services/market-streamer"]


def test_service_image_files_select_that_service():
    full, services, members = detect(
        ["docker/news-http.Dockerfile", "docker/requirements/report-builder.txt"]
    )

    assert not full
    assert services == ["news-http", "report-builder"]
    assert members == ["services/news-http", "services/report-builder"]


def test_unscoped_change_selects_everything():
    full, services, members = detect(["uv.lock"])

    assert full
    assert len(services) == len(
        list((detect_ci_targets.ROOT / "services").glob("*/pyproject.toml"))
    )
    assert "packages/core" in members


def test_app_image_and_docs_changes_select_nothing():
    assert detect(["docker/app.Dockerfile", "docs/x.md", "README.md"]) == (False, [], [])
    assert detect([]) == (False, [], [])
