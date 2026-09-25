"""Build a plain-text report from an evidence session."""

from __future__ import annotations

import os
import time

from . import config
from .evidence import EvidenceLog


def build_report(log: EvidenceLog) -> str:
    """Render the evidence session as markdown."""
    records = log.read_all()
    probes = [r for r in records if r.get("kind") == "probe"]
    verdicts = [r for r in records if r.get("kind") == "verdict"]

    lines = [
        f"# {config.APP_TITLE} 报告",
        "",
        f"- 工具版本：{config.APP_VERSION}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 探测次数：{len(probes)}",
        f"- 判定轮次：{len(verdicts)}",
        "",
        "## 结论",
        "",
    ]
    if verdicts:
        last = verdicts[-1]
        lines += [
            f"- 状态：{last.get('status', '')}",
            f"- 说明：{last.get('headline', '')}",
            "",
        ]
    else:
        lines += ["暂无可用的判定结果。", ""]

    lines += [
        "## 逐次探测记录",
        "",
        "| 时间 | 请求模型 | 服务端声明模型 | 是否换模 | 状态码 | request-id |",
        "|---|---|---|---|---|---|",
    ]
    for record in probes:
        requested = record.get("requested_model", "")
        served = record.get("served_model", "") or "（未捕获）"
        swapped = "是" if served not in ("", "（未捕获）") and served != requested else "否"
        lines.append(
            f"| {record.get('logged_at', '')} | {requested} | {served} | {swapped} | "
            f"{record.get('status_code', '')} | {record.get('request_id', '')} |"
        )

    lines += [
        "",
        "## 判据说明",
        "",
        "唯一判据是「请求模型 == 服务端在回复里声明的模型」。",
        "`x-codex-turn-state` 的长度仅作参考记录，不参与判定：",
        "该说法来自社区逆向，未经官方确认，且 Codex 客户端本身并不校验该值。",
        "",
        "## 原始日志",
        "",
        f"`{log.path}`",
        "",
    ]
    return "\n".join(lines)


def save_report(log: EvidenceLog) -> str:
    """Write the report next to the evidence log; returns the path."""
    config.ensure_dirs()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(config.REPORT_DIR, f"report-{stamp}.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(build_report(log))
    return path
