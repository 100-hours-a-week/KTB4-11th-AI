import importlib.util
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location(
    "archify_pages", Path(__file__).with_name("archify_pages.py")
)
pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pages)


class ArchitecturePagesTests(unittest.TestCase):
    def test_pipeline_keeps_credentials_and_intermediate_files_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "archify/archify"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("skill")
            environment = {
                "LLM_BASE_URL": "http://100.64.0.1/v1",
                "LLM_MODEL": "local-model",
                "LLM_CONTEXT": "32768",
                "LLM_OUTPUT": "8192",
                "LLM_API_KEY": "private-key",
                "GITHUB_SHA": "a" * 40,
                "GITHUB_TOKEN": "must-not-reach-agent",
                "RUNNER_TEMP": directory,
                "PATH": os.environ["PATH"],
                "ARCHIFY_REV": "b" * 40,
                "OPENCODE_VERSION": "1.19.5",
            }

            def run(command, **kwargs):
                if command[0] == "opencode":
                    agent_env = kwargs["env"]
                    self.assertNotIn("GITHUB_TOKEN", agent_env)
                    self.assertNotIn("GITHUB_SHA", agent_env)
                    config_path = Path(agent_env["OPENCODE_CONFIG"])
                    self.assertNotEqual(config_path.parent, kwargs["cwd"])
                    config = json.loads(config_path.read_text())
                    self.assertEqual(config["enabled_providers"], ["local"])
                    self.assertEqual(config["permission"]["*"], "deny")
                    (kwargs["cwd"] / "architecture.json").write_text(
                        '{"meta": {}, "components": '
                        '[{"sources": [{"path": "source.py", "line": 1}]}]}'
                    )
                else:
                    self.assertIn("--repo-root", command)
                    (root / "_site/index.html").write_text("<html>Architecture</html>")
                    (root / "_site/index.delivery.json").write_text("private receipt")
                return SimpleNamespace(returncode=0)

            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(pages.Path, "cwd", return_value=root),
                patch.object(pages, "check_model"),
                patch.object(pages, "copy_sources"),
                patch.object(pages.subprocess, "run", side_effect=run),
            ):
                pages.main()
            self.assertEqual(
                sorted(p.name for p in (root / "_site").iterdir()),
                ["architecture.json", "build.json", "downloads.html", "index.html"],
            )
            self.assertIn('href="./architecture.json"', (root / "_site/downloads.html").read_text())

    def test_only_tailnet_endpoints(self):
        for url in ("http://100.64.0.1:8000/v1", "https://llm.example.ts.net/v1"):
            pages.check_endpoint(url)
        for url in (
            "https://api.openai.com/v1",
            "http://localhost/v1",
            "http://100.128.0.1/v1",
            "https://user:password@llm.example.ts.net/v1",
            "https://llm.example.ts.net/v1?key=x",
            "https://llm.example.ts.net/other",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                pages.check_endpoint(url)

    def test_no_credential_forwarding_on_redirect(self):
        self.assertIsNone(pages.NoRedirect().redirect_request(None, None, 302, "", {}, ""))

    def test_unreachable_model_has_bounded_retries(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError("private endpoint")
        with (
            patch.object(pages.urllib.request, "build_opener", return_value=opener),
            patch.object(pages.time, "sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "three attempts"),
        ):
            pages.check_model("http://100.64.0.1/v1", "model", "secret")
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_missing_model_fails(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"data": [{"id": "other"}]}'
        opener = MagicMock()
        opener.open.return_value = response
        with (
            patch.object(pages.urllib.request, "build_opener", return_value=opener),
            self.assertRaisesRegex(ValueError, "absent"),
        ):
            pages.check_model("http://100.64.0.1/v1", "model", "")

    def test_snapshot_excludes_secrets_instructions_tests_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, target = root / "repo", root / "snapshot"
            names = [
                "services/a/src/main.py",
                "services/a/tests/test_a.py",
                ".env",
                "AGENTS.md",
                ".github/workflows/ci.yaml",
                "infrastructure/private.env",
                "services/a/src/linked.py",
            ]
            for name in names:
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("source")
            (repo / names[-1]).unlink()
            (repo / names[-1]).symlink_to(repo / ".env")
            with patch.object(
                pages.subprocess, "check_output", return_value="\0".join(names).encode()
            ):
                pages.copy_sources(repo, target)
            self.assertEqual(
                sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()),
                [".github/workflows/ci.yaml", "services/a/src/main.py"],
            )

    def test_public_output_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            (site / "index.html").write_text("<html>Architecture</html>")
            (site / "architecture.json").write_text("{}")
            (site / "build.json").write_text(json.dumps({"commit": "abc"}))
            pages.public_files(site, ["secret"])
            for content in ("", "secret", "llm.private.ts.net", "100.100.1.2", "10.1.2.3"):
                (site / "architecture.json").write_text(json.dumps(content) if content else "")
                with self.subTest(content=content), self.assertRaises(ValueError):
                    pages.public_files(site, ["secret"])
            (site / "architecture.json").write_text("invalid JSON")
            with self.assertRaises(json.JSONDecodeError):
                pages.public_files(site, [])


if __name__ == "__main__":
    unittest.main()
