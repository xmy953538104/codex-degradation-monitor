"""Identify the locally installed Codex client so we can speak as it does.

Why this exists
---------------
Sending a hard-coded version string means lying about which client we are, and
the lie can be detected. Instead we discover the real version from this machine
and build the User-Agent from facts.

Discovery order (first hit wins):
  1. ~/.codex/models_cache.json  -- written by Codex itself, contains client_version
  2. `codex --version`           -- authoritative, but spawns a process
  3. nothing found               -- User-Agent is omitted rather than faked
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass

from . import config

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class ClientIdentity:
    """What we know about the local Codex install."""

    version: str = ""
    source: str = ""
    os_label: str = ""
    arch: str = ""

    @property
    def known(self) -> bool:
        return bool(self.version)

    @property
    def user_agent(self) -> str:
        """A User-Agent that tells the truth about this machine.

        Returns "" when the version is unknown -- sending a request with no
        User-Agent is more honest than sending one with an invented version.
        """
        if not self.known:
            return ""
        return f"codex_cli_rs/{self.version} ({self.os_label}; {self.arch}) windows"


def _os_label() -> str:
    """Best-effort human label, e.g. 'Windows 11' or 'Windows 10'."""
    system = platform.system() or "Windows"
    release = platform.release() or ""
    if system.lower().startswith("win"):
        # platform.release() gives '10' on both Windows 10 and 11, so ask the
        # OS itself for the build number and map the well-known boundary.
        build = 0
        try:
            build = int(platform.version().split(".")[-1])
        except (ValueError, IndexError):
            pass
        if build >= 22000:
            return "Windows 11"
        if build:
            return "Windows 10"
        return "Windows"
    return f"{system} {release}".strip()


def _arch() -> str:
    machine = (platform.machine() or "").lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "aarch64"
    if machine in ("x86", "i386", "i686"):
        return "x86"
    return machine or "unknown"


def _from_models_cache() -> tuple[str, str]:
    try:
        with open(config.CODEX_MODELS_CACHE, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return "", ""
    version = data.get("client_version")
    if isinstance(version, str) and _VERSION_RE.search(version):
        return version.strip(), "models_cache.json"
    return "", ""


def _from_codex_cli() -> tuple[str, str]:
    exe = shutil.which("codex")
    if not exe:
        return "", ""
    try:
        out = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "", ""
    match = _VERSION_RE.search((out.stdout or "") + (out.stderr or ""))
    if match:
        return match.group(1), "codex --version"
    return "", ""


def detect_client(prefer_cli: bool = False) -> ClientIdentity:
    """Discover the local Codex version; never raises."""
    probes = (_from_codex_cli, _from_models_cache) if prefer_cli else (_from_models_cache, _from_codex_cli)
    version, source = "", ""
    for probe in probes:
        version, source = probe()
        if version:
            break
    return ClientIdentity(
        version=version,
        source=source or ("未找到" if not version else ""),
        os_label=_os_label(),
        arch=_arch(),
    )
