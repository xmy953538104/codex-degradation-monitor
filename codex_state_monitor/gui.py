"""tkinter user interface.

Threading rule: every network call runs on a worker thread and posts its result
to a queue; the UI thread only ever touches widgets. Without that, a probe that
waits for the server would freeze the window.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import config, credentials, diagnosis, evidence, identity, probe, report


class MonitorApp:
    def __init__(self, root: tk.Tk, auth_path: str | None = None) -> None:
        self.root = root
        self.log = evidence.EvidenceLog()
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.auto_job: str | None = None
        self.client = identity.detect_client()

        root.title(f"{config.APP_TITLE} v{config.APP_VERSION}")
        root.geometry("980x760")
        root.minsize(880, 680)

        self.model_vars: dict[str, tk.BooleanVar] = {}
        self._build_identity_bar()
        self._build_credentials_bar(auth_path)
        self._build_model_bar()
        self._build_controls()
        self._build_results_table()
        self._build_verdict()
        self._build_log()
        self._build_statusbar()

        self.root.after(150, self._drain_events)
        self._refresh_credential_status()

    # ------------------------------------------------------------------ #
    # layout
    # ------------------------------------------------------------------ #
    def _build_identity_bar(self) -> None:
        frame = ttk.LabelFrame(self.root, text="本机 Codex 客户端（用于自适应伪装信息）")
        frame.pack(fill="x", padx=10, pady=(10, 4))
        if self.client.known:
            text = (
                f"版本 {self.client.version}（来源：{self.client.source}）    "
                f"User-Agent：{self.client.user_agent}"
            )
        else:
            text = (
                "未检测到本机 Codex 版本 —— 请求将不携带 User-Agent"
                "（宁可不发，也不伪造版本号）"
            )
        ttk.Label(frame, text=text, wraplength=920, justify="left").pack(
            anchor="w", padx=8, pady=6
        )

    def _build_credentials_bar(self, auth_path: str | None) -> None:
        frame = ttk.LabelFrame(self.root, text="登录凭据（本工具使用独立副本，绝不修改 codex 的文件）")
        frame.pack(fill="x", padx=10, pady=4)

        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(row, text="codex auth.json：").pack(side="left")
        self.auth_var = tk.StringVar(value=auth_path or credentials.discover_codex_auth_path() or "")
        ttk.Entry(row, textvariable=self.auth_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="浏览…", command=self._pick_auth).pack(side="left")
        self.sync_button = ttk.Button(row, text="同步登录凭据", command=self._sync_credentials)
        self.sync_button.pack(side="left", padx=(6, 0))

        self.cred_label = ttk.Label(frame, text="", wraplength=920, justify="left")
        self.cred_label.pack(anchor="w", padx=8, pady=(0, 6))

    def _build_model_bar(self) -> None:
        frame = ttk.LabelFrame(self.root, text="要探测的模型（勾选越少消耗越小）")
        frame.pack(fill="x", padx=10, pady=4)
        inner = ttk.Frame(frame)
        inner.pack(fill="x", padx=8, pady=6)
        for index, model in enumerate(config.KNOWN_MODELS):
            var = tk.BooleanVar(value=(index == 0))
            self.model_vars[model] = var
            ttk.Checkbutton(inner, text=model, variable=var).pack(side="left", padx=(0, 14))

    def _build_controls(self) -> None:
        frame = ttk.Frame(self.root)
        frame.pack(fill="x", padx=10, pady=4)

        self.probe_button = ttk.Button(frame, text="开始检测", command=self._start_probe)
        self.probe_button.pack(side="left")

        self.auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="自动轮询", variable=self.auto_var, command=self._toggle_auto
        ).pack(side="left", padx=(12, 4))
        ttk.Label(frame, text="间隔(分钟)：").pack(side="left")
        self.interval_var = tk.StringVar(value=str(config.DEFAULT_POLL_MINUTES))
        ttk.Spinbox(frame, from_=1, to=60, width=4, textvariable=self.interval_var).pack(side="left")

        ttk.Button(frame, text="导出报告", command=self._export_report).pack(side="right")
        ttk.Button(frame, text="清理本工具文件", command=self._purge).pack(side="right", padx=(0, 8))

    def _build_results_table(self) -> None:
        frame = ttk.LabelFrame(self.root, text="逐次探测结果")
        frame.pack(fill="both", expand=True, padx=10, pady=4)

        columns = ("time", "requested", "served", "swapped", "status", "request_id")
        headings = {
            "time": "时间",
            "requested": "请求的模型",
            "served": "服务端声明的模型",
            "swapped": "是否换模",
            "status": "状态",
            "request_id": "request-id",
        }
        widths = {"time": 70, "requested": 140, "served": 160, "swapped": 80, "status": 70, "request_id": 260}
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=7)
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w")
        self.tree.tag_configure("bad", background="#ffe0e0")
        self.tree.tag_configure("good", background="#e6f6e6")
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y", pady=6)
        self.tree.configure(yscrollcommand=scroll.set)

    def _build_verdict(self) -> None:
        frame = ttk.LabelFrame(self.root, text="结论")
        frame.pack(fill="x", padx=10, pady=4)
        self.verdict_label = ttk.Label(
            frame, text="尚未检测", font=("Segoe UI", 12, "bold"), wraplength=920, justify="left"
        )
        self.verdict_label.pack(anchor="w", padx=8, pady=(6, 2))
        self.verdict_text = tk.Text(frame, height=7, wrap="word", relief="flat", background="#f7f7f7")
        self.verdict_text.pack(fill="x", padx=8, pady=(0, 8))
        self.verdict_text.configure(state="disabled")

    def _build_log(self) -> None:
        frame = ttk.LabelFrame(self.root, text="运行日志")
        frame.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        self.log_text = tk.Text(frame, height=8, wrap="word", relief="flat")
        self.log_text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y", pady=6)
        self.log_text.configure(yscrollcommand=scroll.set, state="disabled")

    def _build_statusbar(self) -> None:
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w").pack(
            fill="x", side="bottom"
        )

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def say(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _pick_auth(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 codex 的 auth.json",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self.auth_var.set(path)

    def _refresh_credential_status(self) -> None:
        try:
            creds = credentials.load_credentials()
        except credentials.CredentialError as exc:
            self.cred_label.configure(text=f"状态：{exc}")
            return
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(creds.synced_at))
        if creds.expired:
            self.cred_label.configure(
                text=f"状态：凭据已过期（同步于 {when}）—— 请点「同步登录凭据」"
            )
        else:
            minutes = max(0, creds.expires_in // 60)
            self.cred_label.configure(
                text=(
                    f"状态：可用    套餐：{creds.plan_type}    "
                    f"账号：{creds.account_id[:12]}…    剩余约 {minutes} 分钟    "
                    f"同步于 {when}"
                )
            )

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    def _sync_credentials(self) -> None:
        source = self.auth_var.get().strip() or None
        result = credentials.sync_from_codex(source)
        if not result.ok:
            self.say(f"同步失败：{result.message}")
            messagebox.showerror(config.APP_TITLE, result.message)
            self._refresh_credential_status()
            return
        self.say(f"已同步凭据：{result.source} → {result.stored}")
        if result.token_expires_in >= 0:
            self.say(f"  凭据剩余有效期约 {result.token_expires_in // 60} 分钟")
        if result.backups_removed:
            self.say(f"  已清理 {len(result.backups_removed)} 个旧副本备份")
        self._refresh_credential_status()

    def _selected_models(self) -> list[str]:
        return [m for m, var in self.model_vars.items() if var.get()]

    def _start_probe(self) -> None:
        if self.busy:
            return
        models = self._selected_models()
        if not models:
            messagebox.showwarning(config.APP_TITLE, "请至少勾选一个模型")
            return
        try:
            creds = credentials.load_credentials()
        except credentials.CredentialError as exc:
            messagebox.showwarning(config.APP_TITLE, str(exc))
            return
        if creds.expired:
            messagebox.showwarning(config.APP_TITLE, "凭据已过期，请先点「同步登录凭据」")
            return

        self.busy = True
        self.probe_button.configure(state="disabled")
        self.status_var.set("检测中…")
        self.say(f"开始检测：{'、'.join(models)}")

        thread = threading.Thread(
            target=self._probe_worker,
            args=(models, creds.access_token, creds.account_id),
            daemon=True,
        )
        thread.start()

    def _probe_worker(self, models: list[str], token: str, account_id: str) -> None:
        try:
            results = []
            for model in models:
                result = probe.probe_model(model, token, account_id, self.client.user_agent)
                results.append(result)
                self.events.put(("result", result))
            self.events.put(("round_done", results))
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected in the UI
            self.events.put(("error", f"{exc.__class__.__name__}: {exc}"))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "result":
                    self._show_result(payload)
                elif kind == "round_done":
                    self._finish_round(payload)
                elif kind == "error":
                    self.say(f"出错：{payload}")
                    self._set_idle()
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)

    def _show_result(self, result: probe.ProbeResult) -> None:
        served = result.served_model or "（未捕获）"
        if result.error:
            swapped, tag = "—", ""
        elif result.substitution:
            swapped, tag = "是", "bad"
        else:
            swapped, tag = "否", "good"
        self.tree.insert(
            "",
            "end",
            values=(
                time.strftime("%H:%M:%S"),
                result.requested_model,
                served,
                swapped,
                result.status_code or result.error[:18],
                result.request_id,
            ),
            tags=(tag,) if tag else (),
        )
        detail = f"{result.requested_model} → {served}"
        if result.error:
            detail += f"  [{result.error}]"
        self.say(f"  {detail}  ({result.elapsed_seconds}s, {result.bytes_read}B)")

    def _finish_round(self, results: list[probe.ProbeResult]) -> None:
        diag = diagnosis.diagnose(results)
        self.log.record_round(results, diag)

        colours = {
            diagnosis.HEALTHY: "#1a7f37",
            diagnosis.DEGRADED: "#b42318",
            diagnosis.UNKNOWN: "#8a6d00",
            diagnosis.FAILED: "#b42318",
        }
        self.verdict_label.configure(
            text=f"{diag.status} —— {diag.headline}",
            foreground=colours.get(diag.status, "#333333"),
        )

        lines = list(diag.details)
        if diag.advice:
            lines.append("")
            lines.append("建议：")
            lines += [f"  · {item}" for item in diag.advice]
        self.verdict_text.configure(state="normal")
        self.verdict_text.delete("1.0", "end")
        self.verdict_text.insert("1.0", "\n".join(lines))
        self.verdict_text.configure(state="disabled")

        self.say(f"本轮结论：{diag.status} —— {diag.headline}")
        self._set_idle()

    def _set_idle(self) -> None:
        self.busy = False
        self.probe_button.configure(state="normal")
        self.status_var.set(f"就绪    证据日志：{self.log.path}")
        self._refresh_credential_status()

    def _toggle_auto(self) -> None:
        if self.auto_var.get():
            self._schedule_auto()
        elif self.auto_job:
            self.root.after_cancel(self.auto_job)
            self.auto_job = None
            self.say("已关闭自动轮询")

    def _schedule_auto(self) -> None:
        try:
            minutes = max(1, int(self.interval_var.get()))
        except ValueError:
            minutes = config.DEFAULT_POLL_MINUTES
        self.say(f"已开启自动轮询，每 {minutes} 分钟一次")
        self.auto_job = self.root.after(minutes * 60_000, self._auto_tick)

    def _auto_tick(self) -> None:
        if not self.auto_var.get():
            return
        self._start_probe()
        self.auto_job = self.root.after(1000, self._schedule_auto)

    def _export_report(self) -> None:
        path = report.save_report(self.log)
        self.say(f"报告已导出：{path}")
        messagebox.showinfo(config.APP_TITLE, f"报告已导出：\n{path}")

    def _purge(self) -> None:
        if not messagebox.askyesno(
            config.APP_TITLE,
            "将删除本工具自己的文件：\n"
            "  · 凭据副本及其备份\n"
            "  · 证据日志与报告\n\n"
            "不会碰 codex 的任何文件。继续？",
        ):
            return
        removed = credentials.purge_state()
        summary = "、".join(f"{k} {v} 个" for k, v in removed.items())
        self.say(f"已清理：{summary}")
        self._refresh_credential_status()
        messagebox.showinfo(config.APP_TITLE, f"已清理：{summary}")


def launch(auth_path: str | None = None) -> None:
    config.ensure_dirs()
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except tk.TclError:
        pass
    MonitorApp(root, auth_path=auth_path)
    root.mainloop()
