"""Append-only evidence log, so a finding can be shown to someone later.

Contains request ids and the account id (which is what makes it useful when
talking to support) but never the access token.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict

from . import config
from .diagnosis import Diagnosis
from .probe import ProbeResult


def _session_path() -> str:
    config.ensure_dirs()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(config.EVIDENCE_DIR, f"session-{stamp}.jsonl")


class EvidenceLog:
    """One JSONL file per app launch."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or _session_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _append(self, record: dict) -> None:
        record = {"logged_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def record_round(self, results: list[ProbeResult], diagnosis: Diagnosis) -> None:
        """Write one probe round: every result plus the verdict."""
        for result in results:
            payload = asdict(result)
            # Belt and braces: the token is never placed in the dataclass, and we
            # drop anything token-shaped that might sneak in via extra headers.
            payload.pop("access_token", None)
            self._append({"kind": "probe", **payload})
        self._append(
            {
                "kind": "verdict",
                "status": diagnosis.status,
                "headline": diagnosis.headline,
                "advisories": diagnosis.advice,
                "substitutions": [
                    {"requested": r.requested_model, "served": r.served_model}
                    for r in diagnosis.substitutions
                ],
            }
        )

    def read_all(self) -> list[dict]:
        if not os.path.isfile(self.path):
            return []
        records = []
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records
