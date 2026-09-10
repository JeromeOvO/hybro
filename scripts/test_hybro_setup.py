"""Offline shell entry-point checks; only temporary scripts and fake uv execute."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class SetupWiringTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="hybro-cli-fixture-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ("scripts", "backend", "home", "bin"):
            (self.root / name).mkdir()
        for name in ("hybro", "hybro-help.txt"):
            shutil.copyfile(ROOT / "scripts" / name, self.root / "scripts" / name)
        self.env = {
            "HOME": str(self.root / "home"),
            "PATH": f"{self.root / 'bin'}:/usr/bin:/bin",
            "CAPTURE": str(self.root / "capture"),
        }
        executable = self.root / "bin/uv"
        executable.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > "$CAPTURE.args"\nfor key in HYBRO_HOME OPENAI_API_KEY UV_PROJECT_ENVIRONMENT; do printenv "$key" > "$CAPTURE.$key" || :; done\nexit "${FAKE_EXIT:-0}"\n'
        )
        executable.chmod(0o700)

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

    def capture(self, key):
        return (self.root / f"capture.{key}").read_text().strip()

    def test_ignores_root_dotenv_and_does_not_evaluate_it(self):
        original = "OPENAI_API_KEY=fixture-root-key\nHYBRO_HOME=/ignored\n$(touch should-not-execute)\n"
        (self.root / ".env").write_text(original)
        result = self.run_cli("setup", "--provider", "openai", trace=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.capture("OPENAI_API_KEY"), "")
        self.assertEqual(self.capture("HYBRO_HOME"), "")
        self.assertEqual((self.root / ".env").read_text(), original)
        self.assertFalse((self.root / "should-not-execute").exists())
        self.assertNotIn("fixture-root-key", result.stdout + result.stderr)

    def test_delegates_to_json_cli_with_uv_dotenv_disabled(self):
        self.assertEqual(self.run_cli("setup", "--help").returncode, 0)
        self.assertEqual(
            self.capture("args").splitlines(),
            [
                str(self.root / "backend"),
                "run",
                "--frozen",
                "--no-env-file",
                "python",
                "-m",
                "common.config.cli",
                "setup",
                "--help",
            ],
        )

    def test_preserves_explicit_location_and_automation_input(self):
        self.env.update(
            HYBRO_HOME=str(self.root / "runtime"),
            OPENAI_API_KEY="fixture-once",
            UV_PROJECT_ENVIRONMENT=str(self.root / "venv"),
        )
        result = self.run_cli("setup", trace=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.capture("OPENAI_API_KEY"), "fixture-once")
        self.assertEqual(self.capture("HYBRO_HOME"), str(self.root / "runtime"))
        self.assertNotIn("fixture-once", result.stdout + result.stderr)

    def test_external_environment_default_and_exit_status(self):
        self.env["FAKE_EXIT"] = "130"
        self.assertEqual(self.run_cli("setup").returncode, 130)
        self.assertEqual(
            self.capture("UV_PROJECT_ENVIRONMENT"),
            str(self.root / "home/.local/share/hybro/venv"),
        )
        self.assertFalse((self.root / ".env").exists())

    def test_start_requires_python_preflight_not_shell_degraded_mode(self):
        self.assertEqual(self.run_cli("start", "--build").returncode, 0)
        self.assertEqual(self.capture("args").splitlines()[-2:], ["start", "--build"])
        self.assertFalse((self.root / "home/.hybro").exists())

    def test_status_does_not_create_configuration(self):
        self.assertEqual(self.run_cli("status").returncode, 0)
        self.assertFalse((self.root / "home/.hybro").exists())

    def test_no_terminal_prints_help_without_starting_uv(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertFalse((self.root / "capture.args").exists())

    def test_config_edits_use_the_same_entry_point(self):
        self.assertEqual(
            self.run_cli("config", "set", "backend.log_level", '"DEBUG"').returncode, 0
        )
        self.assertEqual(
            self.capture("args").splitlines()[-4:],
            ["config", "set", "backend.log_level", '"DEBUG"'],
        )

    def test_only_backend_mounts_runtime_and_no_service_reads_dotenv(self):
        services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
        backend = services["backend"]
        self.assertIn(
            "${HYBRO_HOME:-${HOME}/.hybro}:/var/lib/hybro/runtime", backend["volumes"]
        )
        self.assertIn("HYBRO_CONTAINER=1", backend["environment"])
        self.assertFalse(any("API_KEY=" in value for value in backend["environment"]))
        for name, service in services.items():
            self.assertNotIn("env_file", service)
            if name != "backend":
                self.assertNotIn("/var/lib/hybro/runtime", repr(service))
                self.assertNotIn("HYBRO_HOME", repr(service))


if __name__ == "__main__":
    unittest.main()
