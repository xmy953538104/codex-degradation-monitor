"""Entry point: python run_monitor.py"""

from __future__ import annotations

import argparse
import sys


def _make_stdout_utf8_safe() -> None:
    """Never let console encoding kill the CLI.

    A Windows console defaults to a legacy code page (cp1252 on most CI runners)
    which cannot encode CJK text, so printing anything non-ASCII would raise
    UnicodeEncodeError. Reconfigure once, and degrade to replacement characters
    rather than crashing.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _ascii_reason(exc: BaseException) -> str:
    """Summarize an exception in ASCII, for consoles that cannot encode CJK."""
    text = str(exc)
    lowered = text.lower()
    if "同步" in text or "sync" in lowered:
        return "not synced yet - press the sync button in the GUI"
    if "损坏" in text or "corrupt" in lowered:
        return "credential file is corrupted - sync again"
    if "token" in lowered:
        return "credential file has no access token - sync again"
    return exc.__class__.__name__


def _ascii(text: str) -> str:
    """Render any string as ASCII.

    Paths on the user's machine may contain characters this console cannot
    encode (a CJK Windows user name, for example), so escape anything outside
    ASCII rather than letting the print raise.
    """
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def main() -> None:
    _make_stdout_utf8_safe()

    parser = argparse.ArgumentParser(
        description="Codex 降智监测：核对「请求的模型」是否就是「服务端实际返回的模型」。",
    )
    parser.add_argument(
        "--auth-path",
        default=None,
        help="codex 的 auth.json 路径；省略时自动探测 $CODEX_HOME 与 ~/.codex",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="只做环境自检（版本探测、凭据状态），不打开界面",
    )
    args = parser.parse_args()

    if args.self_test:
        # ASCII-only on purpose: this output must survive any console code page,
        # including non-UTF-8 CI runners.
        from codex_state_monitor import config, credentials, identity

        client = identity.detect_client()
        print(f"tool version   : {config.APP_VERSION}")
        print(f"codex detected : {_ascii(client.version or 'not found')} (source: {_ascii(client.source)})")
        print(f"user-agent     : {_ascii(client.user_agent) or '(omitted)'}")
        print(f"state dir      : {_ascii(config.STATE_DIR)}")
        found = credentials.discover_codex_auth_path()
        print(f"codex auth.json: {_ascii(found) if found else 'not found'}")
        try:
            creds = credentials.load_credentials()
            print(
                f"credential copy: usable (plan {creds.plan_type}, "
                f"~{creds.expires_in // 60} min left)"
            )
        except credentials.CredentialError as exc:
            # The exception text is localized for the GUI. This output must stay
            # ASCII so it survives any console code page, so report it in English.
            print(f"credential copy: unavailable ({_ascii_reason(exc)})")
        return

    launch(auth_path=args.auth_path)


if __name__ == "__main__":
    from codex_state_monitor.gui import launch

    main()
