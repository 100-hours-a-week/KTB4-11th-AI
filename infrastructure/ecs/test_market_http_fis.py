import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infrastructure/ecs/market-http.json"
WORKFLOW = ROOT / ".github/workflows/cd-ecs-v2.yaml"


class MarketHttpFisDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.template = json.loads(TEMPLATE.read_text())
        workflow = WORKFLOW.read_text()
        step = workflow.split("      - name: Verify task definition source", 1)[1]
        step = step.split("\n      - name:", 1)[0]
        self.verify = "jq -e" + step.split("          jq -e", 1)[1]
        step = workflow.split("      - name: Register task definition revision", 1)[1]
        self.render = "python3" + step.split("          python3", 1)[1]
        self.render = self.render.split("\n          arn=", 1)[0]
        self.render = self.render.replace(
            "scripts/render_market_http_task.py",
            shlex.quote(str(ROOT / "scripts/render_market_http_task.py")),
        )

    def run_command(self, command, template):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "task.json"
            source.write_text(json.dumps(template))
            env = {
                **os.environ,
                "TASK_TEMPLATE": str(source),
                "TASK_FAMILY": "stockspoon-v2-prod-market-http",
                "CONTAINER_NAME": "market-http",
                "IMAGE": "example.invalid/market@sha256:" + "a" * 64,
                "POSTGRES_DSN_SECRET_ARN": "arn:aws:ssm:ap-northeast-2:123456789012:parameter/test",
                "MARKET_HTTP_ENV_JSON": json.dumps(
                    {
                        "MARKET_HTTP_TEST": "fixture",
                        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://10.0.11.236:4318",
                    }
                ),
            }
            result = subprocess.run(
                ["bash", "-c", command], cwd=folder, env=env, capture_output=True, text=True
            )
            output = Path(folder) / "next.json"
            return result, json.loads(output.read_text()) if output.exists() else None

    def test_deployment_accepts_complete_fis_configuration(self):
        result, _ = self.run_command(self.verify, self.template)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_deployment_rejects_missing_prerequisites(self):
        for field in ["pidMode", "enableFaultInjection"]:
            with self.subTest(field=field):
                template = json.loads(json.dumps(self.template))
                template.pop(field)
                result, _ = self.run_command(self.verify, template)
                self.assertNotEqual(result.returncode, 0)
        template = json.loads(json.dumps(self.template))
        template["containerDefinitions"] = [
            c for c in template["containerDefinitions"] if c["name"] != "amazon-ssm-agent"
        ]
        result, _ = self.run_command(self.verify, template)
        self.assertNotEqual(result.returncode, 0)

    def test_deployment_rejects_mutable_sidecar_image(self):
        for container in self.template["containerDefinitions"]:
            if container["name"] == "amazon-ssm-agent":
                container["image"] = "public.ecr.aws/aws-fis/aws-fis-ssm-agent-non-root:latest"
        result, _ = self.run_command(self.verify, self.template)
        self.assertNotEqual(result.returncode, 0)

    def test_render_preserves_sidecar_and_task_isolation(self):
        result, rendered = self.run_command(self.render, self.template)
        self.assertEqual(result.returncode, 0, result.stderr)
        original = {c["name"]: c for c in self.template["containerDefinitions"]}
        actual = {c["name"]: c for c in rendered["containerDefinitions"]}
        self.assertEqual(actual["amazon-ssm-agent"], original["amazon-ssm-agent"])
        for key in self.template.keys() - {"containerDefinitions"}:
            self.assertEqual(rendered[key], self.template[key])
        app = {
            k: v
            for k, v in actual["market-http"].items()
            if k not in {"image", "environment", "secrets"}
        }
        expected = {
            k: v
            for k, v in original["market-http"].items()
            if k not in {"image", "environment", "secrets"}
        }
        self.assertEqual(app, expected)
