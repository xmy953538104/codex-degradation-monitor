"""Turn probe observations into a verdict and plain-language advice.

Verdict rule, deliberately narrow
---------------------------------
The verdict is driven ONLY by the authoritative signal: the server named a
model, and that model is not the one we asked for. That is proof.

The `x-codex-turn-state` length is community folklore with no controlled
replication, so it is reported as an informational note and never decides the
verdict on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .probe import ProbeResult

HEALTHY = "健康"
DEGRADED = "降级中"
UNKNOWN = "无法判定"
FAILED = "探测失败"
NO_DATA = "无数据"


@dataclass
class Diagnosis:
    status: str = NO_DATA
    headline: str = ""
    details: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)
    substitutions: list[ProbeResult] = field(default_factory=list)
    healthy_models: list[str] = field(default_factory=list)
    unconfirmed: list[ProbeResult] = field(default_factory=list)


def diagnose(results: list[ProbeResult]) -> Diagnosis:
    """Build a diagnosis from one round of probes."""
    if not results:
        return Diagnosis(status=NO_DATA, headline="尚未执行检测", advice=["点击「开始检测」"])

    working = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    if not working:
        reasons = "；".join(f"{r.requested_model}: {r.error}" for r in failed) or "未知原因"
        return Diagnosis(
            status=FAILED,
            headline="所有探测请求都失败了",
            details=[reasons],
            advice=[
                "确认网络/代理正常，然后点「同步登录凭据」刷新凭据再试一次",
            ],
        )

    substitutions = [r for r in working if r.substitution]
    named = [r for r in working if r.authoritative]
    silent = [r for r in working if not r.authoritative]
    healthy_models = [r.requested_model for r in working if r.authoritative and not r.substitution]

    details: list[str] = []
    advice: list[str] = []

    if substitutions:
        status = DEGRADED
        pairs = "；".join(f"{r.requested_model} → {r.served_model}" for r in substitutions)
        headline = f"发现换模：{pairs}"
        details.append("这是服务端自己在回复里声明的模型，属于硬证据。")
        for r in substitutions:
            if r.safety_faster_model:
                details.append(
                    f"  · 请求 {r.requested_model} 实际返回 {r.served_model}"
                    f"（安全缓冲档位 {r.safety_faster_model}）"
                )
        advice.append("被换模的这些模型，暂时别用来做正经工作。")
        if healthy_models:
            advice.append("当前未被换模的模型：" + "、".join(healthy_models) + "，可临时改用。")
        advice.append("过一段时间再测一次，看是否自行恢复。")
    elif named:
        status = HEALTHY
        headline = "请求的模型就是实际服务的模型"
        details.append("服务端在回复里声明的模型与请求一致：" + "、".join(healthy_models))
        advice.append("没有发现换模。")
    else:
        status = UNKNOWN
        headline = "没能从流里读到服务端声明的模型"
        details.append("请求成功了，但没有捕获到带 model 字段的事件。")
        advice.append("再跑一轮确认；若持续如此，可能是服务端改了事件格式。")

    if silent and named:
        advice.append(
            f"{len(silent)} 个模型未捕获到声明模型："
            + "、".join(r.requested_model for r in silent)
        )

    if failed:
        advice.append(f"{len(failed)} 个模型探测失败：" + "；".join(f"{r.requested_model}: {r.error}" for r in failed))

    # Informational only -- never changes the verdict.
    state_lengths = {r.turn_state_length for r in working if r.turn_state_length}
    if state_lengths:
        shown = "、".join(str(v) for v in sorted(state_lengths))
        details.append(
            f"参考信息：x-codex-turn-state 长度 = {shown}（社区传言与降级相关，"
            "但无受控复现，且 codex 客户端本身完全不校验该值，故不作为判据）"
        )

    quota = max((r.primary_used_percent for r in working), default=-1)
    if quota >= 0:
        details.append(f"本周配额已用 {quota}%")
        if quota >= config.QUOTA_HIGH_PERCENT:
            advice.append(f"配额已用 {quota}%，接近耗尽，重任务建议等窗口重置。")

    plan = next((r.plan_type for r in working if r.plan_type), "")
    if plan:
        details.append(f"套餐类型：{plan}")

    return Diagnosis(
        status=status,
        headline=headline,
        details=details,
        advice=advice,
        substitutions=substitutions,
        healthy_models=healthy_models,
        unconfirmed=silent,
    )
