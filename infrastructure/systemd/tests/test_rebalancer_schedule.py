from configparser import ConfigParser
from pathlib import Path

UNITS = Path(__file__).resolve().parents[1]


def test_failed_explanations_remain_failed_without_automatic_restarts():
    unit = ConfigParser(interpolation=None)
    unit.read(UNITS / "ktb-portfolio-rebalancer.service.in")

    service = unit["Service"]
    assert service["Restart"] == "on-failure"
    assert service["RestartMode"] == "direct"
    assert service["RestartPreventExitStatus"].split() == ["2"]
    assert "2" not in service.get("SuccessExitStatus", "").split()
    assert unit["Unit"]["OnFailure"] == "ktb-scheduled-job-failure@portfolio-rebalancer.service"
