"""Project identity, tunables, and filesystem locations.

Everything a user might reasonably want to change lives here.
"""

from __future__ import annotations

import os

APP_NAME = "codex-degradation-monitor"
APP_TITLE = "Codex 降智监测"
APP_VERSION = "0.1.0"

# --- Where this tool keeps its own state ------------------------------------
# Deliberately NOT inside ~/.codex: we never write into Codex's own directory.
STATE_DIR = os.path.expanduser(
    os.environ.get("CODEX_MONITOR_STATE_DIR") or os.path.join("~", ".codex_state_monitor")
)
CREDENTIALS_PATH = os.path.join(STATE_DIR, "credentials.json")
EVIDENCE_DIR = os.path.join(STATE_DIR, "evidence")
REPORT_DIR = os.path.join(STATE_DIR, "reports")

# --- Where the real Codex client keeps its login ----------------------------
CODEX_HOME = os.path.expanduser(os.environ.get("CODEX_HOME") or os.path.join("~", ".codex"))
CODEX_AUTH_PATH = os.path.join(CODEX_HOME, "auth.json")
CODEX_MODELS_CACHE = os.path.join(CODEX_HOME, "models_cache.json")

# --- Probe endpoint ---------------------------------------------------------
RESPONSES_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
PROBE_PROMPT = "Reply with exactly: OK"
PROBE_REASONING_EFFORT = "low"

# Models offered in the GUI. The first is checked by default.
KNOWN_MODELS = (
    "gpt-6-astra",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
)

# --- Networking -------------------------------------------------------------
CONNECT_TIMEOUT_SECONDS = 15
READ_TIMEOUT_SECONDS = 45
MAX_STREAM_BYTES = 20000
# Stop as soon as the server has named the model AND we have read this much.
MIN_BYTES_BEFORE_STOP = 2000

# --- Polling ----------------------------------------------------------------
DEFAULT_POLL_MINUTES = 3
QUOTA_HIGH_PERCENT = 85

# --- Credential copy retention ----------------------------------------------
# Superseded credential copies are kept as credentials.<timestamp>.json.bak so a
# bad sync can be rolled back, but only the newest few are retained.
MAX_CREDENTIAL_BACKUPS = 3


def ensure_dirs() -> None:
    """Create this tool's own directories. Never touches ~/.codex."""
    for path in (STATE_DIR, EVIDENCE_DIR, REPORT_DIR):
        os.makedirs(path, exist_ok=True)
