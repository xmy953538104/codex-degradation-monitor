"""Entry point: python run_monitor.py"""

from __future__ import annotations

import argparse

from codex_state_monitor.gui import launch


def main() -> None:
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
        from codex_state_monitor import config, credentials, identity

        client = identity.detect_client()
        print(f"工具版本      : {config.APP_VERSION}")
        print(f"检测到的 codex : {client.version or '未找到'}（来源：{client.source}）")
        print(f"User-Agent     : {client.user_agent or '（不发）'}")
        print(f"状态目录       : {config.STATE_DIR}")
        found = credentials.discover_codex_auth_path()
        print(f"codex auth.json: {found or '未找到'}")
        try:
            creds = credentials.load_credentials()
            print(f"凭据副本       : 可用（套餐 {creds.plan_type}，剩余约 {creds.expires_in // 60} 分钟）")
        except credentials.CredentialError as exc:
            print(f"凭据副本       : {exc}")
        return

    launch(auth_path=args.auth_path)


if __name__ == "__main__":
    main()
