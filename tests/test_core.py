"""Tests runnable with the standard library alone: `python -m unittest discover -s tests`

No pytest, no third-party packages. This is what lets the project be developed
and verified without installing anything.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codex_state_monitor import config, credentials, diagnosis, evidence, identity, probe, report  # noqa: E402


def _jwt(claims: dict) -> str:
    """Build an unsigned JWT-shaped string with the given payload claims."""
    import base64

    def part(obj: dict) -> str:
        raw = json.dumps(obj).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #
class TestIdentity(unittest.TestCase):
    def test_user_agent_uses_real_version(self):
        ident = identity.ClientIdentity(version="0.153.4", os_label="Windows 11", arch="x86_64")
        self.assertEqual(ident.user_agent, "codex_cli_rs/0.153.4 (Windows 11; x86_64) windows")

    def test_unknown_version_omits_user_agent(self):
        """Better to send nothing than to invent a version."""
        ident = identity.ClientIdentity()
        self.assertFalse(ident.known)
        self.assertEqual(ident.user_agent, "")

    def test_detect_reads_this_machine(self):
        ident = identity.detect_client()
        self.assertTrue(ident.os_label, "should always know the OS label")
        self.assertTrue(ident.arch)
        if ident.known:
            self.assertIn(ident.version, ident.user_agent)


# --------------------------------------------------------------------------- #
# credential isolation and backup pruning
# --------------------------------------------------------------------------- #
class TestCredentials(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_state = config.STATE_DIR
        self._old_creds = config.CREDENTIALS_PATH
        self._old_evidence = config.EVIDENCE_DIR
        self._old_reports = config.REPORT_DIR
        config.STATE_DIR = self._tmp.name
        config.CREDENTIALS_PATH = os.path.join(self._tmp.name, "credentials.json")
        config.EVIDENCE_DIR = os.path.join(self._tmp.name, "evidence")
        config.REPORT_DIR = os.path.join(self._tmp.name, "reports")

    def tearDown(self):
        config.STATE_DIR = self._old_state
        config.CREDENTIALS_PATH = self._old_creds
        config.EVIDENCE_DIR = self._old_evidence
        config.REPORT_DIR = self._old_reports
        self._tmp.cleanup()

    def _fake_auth(self, token: str = "tok") -> str:
        path = os.path.join(self._tmp.name, "auth.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "tokens": {
                        "access_token": _jwt({"exp": int(time.time()) + 3600}),
                        "account_id": "acct-1",
                        "id_token": _jwt({"https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}}),
                        "refresh_token": "refresh-" + token,
                    }
                },
                handle,
            )
        return path

    def test_sync_never_writes_to_codex_auth(self):
        """The whole point: the source file must be byte-identical afterwards."""
        source = self._fake_auth()
        with open(source, "rb") as handle:
            before = handle.read()
        result = credentials.sync_from_codex(source)
        self.assertTrue(result.ok, result.message)
        with open(source, "rb") as handle:
            after = handle.read()
        self.assertEqual(before, after, "syncing must not modify codex's auth.json")

    def test_sync_creates_private_copy(self):
        source = self._fake_auth()
        result = credentials.sync_from_codex(source)
        self.assertTrue(result.ok)
        self.assertTrue(os.path.isfile(config.CREDENTIALS_PATH))
        loaded = credentials.load_credentials()
        self.assertEqual(loaded.account_id, "acct-1")
        self.assertEqual(loaded.plan_type, "plus")
        self.assertGreater(loaded.expires_in, 0)

    def test_backups_are_pruned(self):
        """Stale copies must not accumulate -- this was an explicit requirement."""
        source = self._fake_auth()
        for i in range(8):
            credentials.sync_from_codex(source)
            time.sleep(0.01)
        backups = [
            n
            for n in os.listdir(config.STATE_DIR)
            if n.startswith("credentials.") and n.endswith(".bak")
        ]
        self.assertLessEqual(len(backups), config.MAX_CREDENTIAL_BACKUPS)

    def test_missing_auth_reports_clearly(self):
        result = credentials.sync_from_codex(os.path.join(self._tmp.name, "nope.json"))
        self.assertFalse(result.ok)
        self.assertIn("codex login", result.message)

    def test_load_without_sync_raises_helpfully(self):
        with self.assertRaises(credentials.CredentialError) as ctx:
            credentials.load_credentials()
        self.assertIn("同步", str(ctx.exception))

    def test_purge_removes_only_our_files(self):
        source = self._fake_auth()
        credentials.sync_from_codex(source)
        config.ensure_dirs()
        with open(os.path.join(config.EVIDENCE_DIR, "x.jsonl"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        removed = credentials.purge_state()
        self.assertGreaterEqual(removed["credentials"], 1)
        self.assertGreaterEqual(removed["evidence"], 1)
        self.assertTrue(os.path.isfile(source), "purge must not touch codex's auth.json")


# --------------------------------------------------------------------------- #
# probe parsing
# --------------------------------------------------------------------------- #
class TestProbeParsing(unittest.TestCase):
    def test_extracts_model_from_response_created(self):
        event = {"type": "response.created", "response": {"id": "r1", "model": "gpt-5.6-luna"}}
        self.assertEqual(probe._model_from_event(event), ("gpt-5.6-luna", "response.created"))

    def test_extracts_top_level_model(self):
        self.assertEqual(probe._model_from_event({"type": "x", "model": "m"}), ("m", "x"))

    def test_ignores_events_without_model(self):
        self.assertEqual(probe._model_from_event({"type": "response.output_text.delta"}), ("", ""))

    def test_substitution_requires_both_sides(self):
        self.assertFalse(probe.ProbeResult(requested_model="a", served_model="").substitution)
        self.assertFalse(probe.ProbeResult(requested_model="", served_model="b").substitution)
        self.assertFalse(probe.ProbeResult(requested_model="a", served_model="a").substitution)
        self.assertTrue(probe.ProbeResult(requested_model="a", served_model="b").substitution)

    def test_authoritative_only_when_server_named_a_model(self):
        self.assertFalse(probe.ProbeResult(requested_model="a").authoritative)
        self.assertTrue(probe.ProbeResult(requested_model="a", served_model="a").authoritative)


# --------------------------------------------------------------------------- #
# diagnosis
# --------------------------------------------------------------------------- #
class TestDiagnosis(unittest.TestCase):
    def _ok(self, requested, served, **kw):
        return probe.ProbeResult(
            requested_model=requested, served_model=served, status_code=200, **kw
        )

    def test_no_data(self):
        self.assertEqual(diagnosis.diagnose([]).status, diagnosis.NO_DATA)

    def test_all_failed(self):
        results = [probe.ProbeResult(requested_model="a", error="网络错误：URLError")]
        self.assertEqual(diagnosis.diagnose(results).status, diagnosis.FAILED)

    def test_match_is_healthy(self):
        diag = diagnosis.diagnose([self._ok("gpt-6-astra", "gpt-6-astra")])
        self.assertEqual(diag.status, diagnosis.HEALTHY)

    def test_mismatch_is_degraded(self):
        diag = diagnosis.diagnose([self._ok("gpt-6-astra", "gpt-5.6-luna")])
        self.assertEqual(diag.status, diagnosis.DEGRADED)
        self.assertEqual(len(diag.substitutions), 1)

    def test_turn_state_length_never_decides_verdict(self):
        """A 'degraded-looking' turn-state with a matching model stays healthy."""
        diag = diagnosis.diagnose([self._ok("gpt-6-astra", "gpt-6-astra", turn_state_length=312)])
        self.assertEqual(diag.status, diagnosis.HEALTHY)
        self.assertTrue(any("turn-state" in d for d in diag.details))

    def test_uncaptured_model_is_unknown_not_healthy(self):
        diag = diagnosis.diagnose([probe.ProbeResult(requested_model="a", status_code=200)])
        self.assertEqual(diag.status, diagnosis.UNKNOWN)

    def test_partial_mismatch_reports_healthy_alternatives(self):
        diag = diagnosis.diagnose(
            [self._ok("gpt-6-astra", "gpt-5.6-luna"), self._ok("gpt-5.6-sol", "gpt-5.6-sol")]
        )
        self.assertEqual(diag.status, diagnosis.DEGRADED)
        self.assertIn("gpt-5.6-sol", diag.healthy_models)


# --------------------------------------------------------------------------- #
# evidence + report
# --------------------------------------------------------------------------- #
class TestEvidenceAndReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_state = config.STATE_DIR
        self._old_evidence = config.EVIDENCE_DIR
        self._old_reports = config.REPORT_DIR
        config.STATE_DIR = self._tmp.name
        config.EVIDENCE_DIR = os.path.join(self._tmp.name, "evidence")
        config.REPORT_DIR = os.path.join(self._tmp.name, "reports")

    def tearDown(self):
        config.STATE_DIR = self._old_state
        config.EVIDENCE_DIR = self._old_evidence
        config.REPORT_DIR = self._old_reports
        self._tmp.cleanup()

    def test_round_trip_and_no_secrets(self):
        log = evidence.EvidenceLog()
        results = [
            probe.ProbeResult(
                requested_model="gpt-6-astra",
                served_model="gpt-5.6-luna",
                status_code=200,
                request_id="req_1",
            )
        ]
        diag = diagnosis.diagnose(results)
        log.record_round(results, diag)

        records = log.read_all()
        self.assertEqual(len(records), 2)
        blob = json.dumps(records)
        self.assertNotIn("access_token", blob)
        self.assertNotIn("Bearer", blob)

        text = report.build_report(log)
        self.assertIn("gpt-6-astra", text)
        self.assertIn("gpt-5.6-luna", text)
        self.assertIn(diagnosis.DEGRADED, text)

        path = report.save_report(log)
        self.assertTrue(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
