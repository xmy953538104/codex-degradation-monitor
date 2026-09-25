"""An isolated copy of the Codex login.

Design rule: this module READS Codex's auth.json and WRITES only inside this
tool's own directory. Codex's own file is never modified, so a sync here can
never invalidate the login the Codex client is holding.

Why a copy instead of using auth.json directly
---------------------------------------------
The OAuth refresh token rotates: whoever refreshes first invalidates the other
holder's token. Codex refreshes on its own schedule and we cannot know when, so
sharing one file between two processes ends in a race that can break the Codex
login. A private copy turns that race into a non-event: worst case our copy goes
stale and the user presses "同步登录凭据" again.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import time
from dataclasses import dataclass, field

from . import config


class CredentialError(RuntimeError):
    """Raised when no usable credentials are available."""


def _decode_jwt_claims(token: str) -> dict:
    """Read a JWT payload without verifying it. No network, no secrets printed."""
    try:
        part = token.split(".")[1]
    except IndexError:
        return {}
    part += "=" * (-len(part) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(part))
    except (ValueError, json.JSONDecodeError):
        return {}


def discover_codex_auth_path() -> str | None:
    """Find the real Codex auth.json. Read-only."""
    candidates = [
        os.path.join(config.CODEX_HOME, "auth.json"),
        os.path.expanduser(os.path.join("~", ".codex", "auth.json")),
    ]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(os.path.join(xdg, "codex", "auth.json"))
    candidates.append(os.path.expanduser(os.path.join("~", ".config", "codex", "auth.json")))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def _atomic_write_json(path: str, payload: dict) -> None:
    """Write via temp file + replace so a crash cannot leave a half-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _prune_backups(prefix: str, keep: int) -> list[str]:
    """Delete superseded copies beyond the newest `keep`. Returns removed paths.

    This is what keeps the state directory from growing without bound after many
    syncs.
    """
    directory = os.path.dirname(prefix)
    stem = os.path.basename(prefix)
    try:
        entries = [
            os.path.join(directory, name)
            for name in os.listdir(directory)
            if name.startswith(stem + ".") and name.endswith(".bak")
        ]
    except OSError:
        return []
    entries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    removed = []
    for stale in entries[keep:]:
        try:
            os.remove(stale)
            removed.append(stale)
        except OSError:
            pass
    return removed


@dataclass
class SyncResult:
    ok: bool
    message: str
    source: str = ""
    stored: str = ""
    account_id: str = ""
    plan_type: str = ""
    token_expires_in: int = -1
    backups_removed: list[str] = field(default_factory=list)


def sync_from_codex(source_path: str | None = None) -> SyncResult:
    """Copy Codex's login into this tool's own file. Never writes to Codex."""
    source = source_path or discover_codex_auth_path()
    if not source or not os.path.isfile(source):
        return SyncResult(False, "找不到 codex 的 auth.json，请先运行 `codex login`")

    try:
        with open(source, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return SyncResult(False, f"读取失败：{exc.__class__.__name__}")

    tokens = document.get("tokens") or {}
    access = tokens.get("access_token")
    if not access:
        return SyncResult(False, "auth.json 里没有 access token，请先运行 `codex login`")

    claims = _decode_jwt_claims(access)
    expires_at = int(claims.get("exp", 0) or 0)
    remaining = expires_at - int(time.time()) if expires_at else -1

    config.ensure_dirs()

    # Keep the previous copy as a rollback point (bounded, pruned below).
    removed: list[str] = []
    if os.path.isfile(config.CREDENTIALS_PATH):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"{config.CREDENTIALS_PATH}.{stamp}.bak"
        try:
            shutil.copy2(config.CREDENTIALS_PATH, backup)
        except OSError:
            pass
        removed = _prune_backups(config.CREDENTIALS_PATH, config.MAX_CREDENTIAL_BACKUPS)

    payload = {
        "synced_at": int(time.time()),
        "source": source,
        "tokens": {
            "access_token": access,
            "account_id": tokens.get("account_id", ""),
            "id_token": tokens.get("id_token", ""),
        },
    }
    try:
        _atomic_write_json(config.CREDENTIALS_PATH, payload)
    except OSError as exc:
        return SyncResult(False, f"写入失败：{exc.__class__.__name__}")

    account_id = tokens.get("account_id", "")
    plan = _decode_jwt_claims(tokens.get("id_token", "")).get("https://api.openai.com/auth", {})
    plan_type = plan.get("chatgpt_plan_type", "unknown") if isinstance(plan, dict) else "unknown"

    return SyncResult(
        ok=True,
        message="同步完成",
        source=source,
        stored=config.CREDENTIALS_PATH,
        account_id=account_id,
        plan_type=plan_type,
        token_expires_in=remaining,
        backups_removed=removed,
    )


@dataclass
class Credentials:
    access_token: str
    account_id: str
    plan_type: str
    expires_in: int
    synced_at: int
    source: str

    @property
    def expired(self) -> bool:
        return self.expires_in <= 0


def load_credentials() -> Credentials:
    """Load this tool's own copy. Raises CredentialError when unusable."""
    if not os.path.isfile(config.CREDENTIALS_PATH):
        raise CredentialError("尚未同步登录凭据，请点「同步登录凭据」")
    try:
        with open(config.CREDENTIALS_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialError(f"凭据文件损坏：{exc.__class__.__name__}") from exc

    tokens = payload.get("tokens") or {}
    access = tokens.get("access_token")
    if not access:
        raise CredentialError("凭据副本里没有 access token，请重新同步")

    claims = _decode_jwt_claims(access)
    expires_at = int(claims.get("exp", 0) or 0)
    remaining = expires_at - int(time.time()) if expires_at else -1
    plan = _decode_jwt_claims(tokens.get("id_token", "")).get("https://api.openai.com/auth", {})

    return Credentials(
        access_token=access,
        account_id=tokens.get("account_id", ""),
        plan_type=plan.get("chatgpt_plan_type", "unknown") if isinstance(plan, dict) else "unknown",
        expires_in=remaining,
        synced_at=int(payload.get("synced_at", 0) or 0),
        source=payload.get("source", ""),
    )


def purge_state(include_evidence: bool = True) -> dict[str, int]:
    """Delete this tool's own files. Never touches anything under ~/.codex.

    Backups are always removed; the credential copy and evidence/report files are
    removed when `include_evidence` is true.
    """
    removed = {"credentials": 0, "backups": 0, "evidence": 0, "reports": 0}
    targets: list[tuple[str, str]] = []

    if include_evidence:
        targets.append((config.CREDENTIALS_PATH, "credentials"))

    if os.path.isdir(config.STATE_DIR):
        for name in os.listdir(config.STATE_DIR):
            path = os.path.join(config.STATE_DIR, name)
            if name.startswith("credentials.") and name.endswith(".bak"):
                targets.append((path, "backups"))
        for directory, kind in ((config.EVIDENCE_DIR, "evidence"), (config.REPORT_DIR, "reports")):
            if include_evidence and os.path.isdir(directory):
                for name in os.listdir(directory):
                    targets.append((os.path.join(directory, name), kind))

    for path, kind in targets:
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            removed[kind] += 1
        except OSError:
            pass
    return removed
