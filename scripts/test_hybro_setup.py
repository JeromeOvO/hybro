"""Offline CLI wiring checks. Only temporary scripts and fake uv/Docker execute.

Run with Python directly; no backend imports or functional-test collection.
"""

import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class SetupWiringTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hybro-cli-fixture-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        (self.root / "backend").mkdir()
        (self.root / "home").mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("hybro", "hybro-help.txt"):
            shutil.copyfile(ROOT / "scripts" / name, self.root / "scripts" / name)
        self.env = {
            "HOME": str(self.root / "home"),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "CAPTURE": str(self.root / "capture"),
        }
        self.stub(
            "uv",
            'printf "%s\\n" "$PWD" "$@" > "$CAPTURE.args"\n'
            "for key in HYBRO_HOME OPENAI_API_KEY DEEPSEEK_API_KEY ANTHROPIC_API_KEY OPENAI_BASE_URL UV_PROJECT_ENVIRONMENT; do\n"
            '  printenv "$key" > "$CAPTURE.$key" || :\n'
            'done\nexit "${FAKE_EXIT:-0}"\n',
        )
        self.stub(
            "docker",
            'if [ "$2" = version ]; then echo 2.24.0; else printf "%s\\n" "$@" > "$CAPTURE.docker"; fi\n',
        )
        self.stub("python3", "exit 0\n")

    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o700)

    def run_cli(self, *args, trace=False):
        return subprocess.run(
            [
                "/bin/sh",
                *(["-x"] if trace else []),
                str(self.root / "scripts/hybro"),
                *args,
            ],
            cwd=self.root / "home",
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def captured(self, key):
        return (self.root / f"capture.{key}").read_text().strip()

    def test_setup_forwards_args_and_root_values_without_shell_evaluation(self):
        runtime = self.root / "private config"
        (self.root / ".env").write_text(
            f'HYBRO_HOME="{runtime}"\n'
            'OPENAI_API_KEY="fixture-root-key"\n'
            "DEEPSEEK_API_KEY='fixture-deepseek'\n"
            "ANTHROPIC_API_KEY=fixture-anthropic # comment\n"
            "OPENAI_BASE_URL=https://fixture.invalid/v1\n"
            "$(touch should-not-execute)\n"
        )
        result = self.run_cli(
            "setup", "--provider", "openai", "--image-model", "none", trace=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.captured("HYBRO_HOME"), str(runtime))
        self.assertEqual(self.captured("OPENAI_API_KEY"), "fixture-root-key")
        self.assertEqual(self.captured("DEEPSEEK_API_KEY"), "fixture-deepseek")
        self.assertEqual(self.captured("ANTHROPIC_API_KEY"), "fixture-anthropic")
        self.assertEqual(self.captured("OPENAI_BASE_URL"), "https://fixture.invalid/v1")
        self.assertEqual(
            self.captured("args").splitlines(),
            [
                str(self.root / "backend"),
                "run",
                "--frozen",
                "python",
                "-m",
                "llm_gateway.setup_cli",
                "--provider",
                "openai",
                "--image-model",
                "none",
            ],
        )
        self.assertNotIn("fixture-root-key", result.stdout + result.stderr)
        self.assertFalse((self.root / "should-not-execute").exists())
        self.assertFalse((self.root / "capture.docker").exists())

    def test_shell_precedence_including_explicit_empty_and_environment_override(self):
        (self.root / ".env").write_text(
            "OPENAI_API_KEY=fixture-root\nDEEPSEEK_API_KEY=fixture-root\n"
        )
        self.env.update(
            OPENAI_API_KEY="fixture-shell",
            DEEPSEEK_API_KEY="",
            UV_PROJECT_ENVIRONMENT=str(self.root / "venv"),
        )
        self.assertEqual(self.run_cli("setup", "--help").returncode, 0)
        self.assertEqual(self.captured("OPENAI_API_KEY"), "fixture-shell")
        self.assertEqual(self.captured("DEEPSEEK_API_KEY"), "")
        self.assertEqual(
            self.captured("UV_PROJECT_ENVIRONMENT"), str(self.root / "venv")
        )

    def test_no_config_uses_home_default_and_propagates_exit(self):
        self.env["FAKE_EXIT"] = "130"
        self.assertEqual(self.run_cli("setup").returncode, 130)
        self.assertEqual(self.captured("HYBRO_HOME"), "")
        self.assertEqual(
            self.captured("UV_PROJECT_ENVIRONMENT"),
            str(self.root / "home/.local/share/hybro/venv"),
        )
        self.assertFalse((self.root / ".env").exists())

    def test_start_creates_private_bind_source_without_provider_calls(self):
        result = self.run_cli("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            stat.S_IMODE((self.root / "home/.hybro").stat().st_mode), 0o700
        )
        self.assertEqual(
            self.captured("docker").splitlines(),
            ["compose", "up", "-d", "--remove-orphans"],
        )
        self.assertFalse((self.root / "capture.args").exists())

    def test_status_does_not_create_config_directory(self):
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "home/.hybro").exists())

    def test_tui_launches_menu_without_loading_credentials_or_running_docker(self):
        (self.root / ".env").write_text("OPENAI_API_KEY=fixture-not-for-menu\n")
        result = self.run_cli("tui")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.captured("args").splitlines(),
            [
                str(self.root / "backend"),
                "run",
                "--frozen",
                "python",
                "-m",
                "llm_gateway.cli_tui",
            ],
        )
        self.assertEqual(self.captured("OPENAI_API_KEY"), "")
        self.assertFalse((self.root / "capture.docker").exists())
        self.assertFalse((self.root / "home/.hybro").exists())

    def test_no_terminal_keeps_help_without_launching_menu(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertFalse((self.root / "capture.args").exists())
        self.assertFalse((self.root / "capture.docker").exists())

    def test_tui_rejects_unexpected_arguments_without_echo(self):
        result = self.run_cli("tui", "fixture-secret")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)
        self.assertFalse((self.root / "capture.args").exists())

    def test_only_backend_mounts_runtime_and_forwards_selected_keys(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        backend = compose["services"]["backend"]
        self.assertIn(
            "${HYBRO_HOME:-${HOME}/.hybro}:/var/lib/hybro/runtime", backend["volumes"]
        )
        self.assertIn("HYBRO_HOME=/var/lib/hybro/runtime", backend["environment"])
        for key in (
            "OPENAI_API_KEY",
            "DEEPSEEK_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_BASE_URL",
        ):
            self.assertIn(f"{key}=${{{key}:-}}", backend["environment"])
        for name, service in compose["services"].items():
            if name != "backend":
                self.assertNotIn("/var/lib/hybro/runtime", repr(service))
                self.assertNotIn("HYBRO_HOME", repr(service))


if __name__ == "__main__":
    unittest.main()
