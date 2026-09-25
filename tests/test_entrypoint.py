"""Regression tests for the CLI entry point.

The bug these guard against: on a Windows runner whose console code page is
cp1252, printing CJK text raised UnicodeEncodeError and failed the build. The
entry point must survive any console encoding.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import run_monitor  # noqa: E402


class TestStdoutEncoding(unittest.TestCase):
    def test_reconfigure_is_safe_when_unsupported(self):
        """A stream without reconfigure() must not raise."""

        class Dumb:
            def write(self, _):
                return 0

        original_out, original_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = Dumb(), Dumb()
        try:
            run_monitor._make_stdout_utf8_safe()
        finally:
            sys.stdout, sys.stderr = original_out, original_err

    def test_ascii_output_survives_cp1252_console(self):
        """The real scenario: run --self-test with a legacy code page console."""
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "cp1252"
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "run_monitor.py"), "--self-test"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, f"stderr was: {proc.stderr}")
        self.assertIn("tool version", proc.stdout)
        self.assertIn("codex detected", proc.stdout)

    def test_self_test_output_stays_ascii(self):
        """Keeping the CLI output ASCII is what makes it portable.

        The state directory is redirected to an empty temp dir so this asserts
        the tool's own output rather than whatever happens to be on the machine
        running the test -- the first version of this test passed locally only
        because credentials already existed here.
        """
        with tempfile.TemporaryDirectory() as state_dir:
            env = dict(os.environ)
            env["CODEX_MONITOR_STATE_DIR"] = state_dir
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "run_monitor.py"), "--self-test"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=120,
            )
        self.assertEqual(proc.returncode, 0, f"stderr was: {proc.stderr}")
        try:
            proc.stdout.encode("ascii")
        except UnicodeEncodeError as exc:
            self.fail(f"self-test output must stay ASCII, found: {exc}")

    def test_self_test_survives_missing_credentials(self):
        """With no credential copy the tool must report it, not crash."""
        with tempfile.TemporaryDirectory() as state_dir:
            env = dict(os.environ)
            env["CODEX_MONITOR_STATE_DIR"] = state_dir
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "run_monitor.py"), "--self-test"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=120,
            )
        self.assertEqual(proc.returncode, 0, f"stderr was: {proc.stderr}")
        self.assertIn("credential copy:", proc.stdout)
        self.assertIn("unavailable", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
