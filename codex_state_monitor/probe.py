"""Send a minimal streaming request and read the model the server names.

This is the heart of the tool. Instead of intercepting and decrypting the Codex
client's own traffic, we make our own request with the user's own credentials
and ask the server a question only it can answer: which model is actually
serving this?

Evidence chain:
  * we put `model = X` in the request body (a fact we control)
  * the server's `response.created` event carries the model it selected
  * X != served  is proof of substitution, and needs no heuristics

Only the standard library is used, so the packaged build carries no vendored
third-party code.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from . import config


@dataclass
class ProbeResult:
    """What one probe observed. Never contains the access token."""

    requested_model: str = ""
    served_model: str = ""
    served_event: str = ""
    status_code: int = 0
    error: str = ""
    elapsed_seconds: float = 0.0

    turn_state_length: int = 0
    safety_buffering_enabled: bool = False
    safety_faster_model: str = ""
    plan_type: str = ""
    active_limit: str = ""
    primary_used_percent: int = -1
    primary_window_minutes: int = 0
    primary_reset_after_seconds: int = 0
    request_id: str = ""
    cf_ray: str = ""
    bytes_read: int = 0
    extra_headers: dict = field(default_factory=dict)

    @property
    def substitution(self) -> bool:
        """True only when we have hard proof of substitution."""
        return bool(
            self.served_model and self.requested_model and self.served_model != self.requested_model
        )

    @property
    def ok(self) -> bool:
        return not self.error and self.status_code == 200

    @property
    def authoritative(self) -> bool:
        """True when the server named a model, which is the only real evidence."""
        return bool(self.served_model)


def _model_from_event(event: dict) -> tuple[str, str]:
    """Pull the served model out of one SSE event, if it names one."""
    event_type = str(event.get("type", ""))
    candidates = (
        event.get("model"),
        (event.get("response") or {}).get("model") if isinstance(event.get("response"), dict) else None,
        (event.get("item") or {}).get("model") if isinstance(event.get("item"), dict) else None,
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate, event_type
    return "", ""


def probe_model(
    model: str,
    access_token: str,
    account_id: str,
    user_agent: str = "",
) -> ProbeResult:
    """Probe one model. Never raises for network or HTTP problems."""
    result = ProbeResult(requested_model=model)

    headers = {
        "authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "openai-beta": "responses=experimental",
        "originator": "codex_cli_rs",
        "session_id": str(uuid.uuid4()),
        "accept": "text/event-stream",
        "content-type": "application/json",
    }
    # Only claim to be a client version we actually verified on this machine.
    if user_agent:
        headers["user-agent"] = user_agent

    body = {
        "model": model,
        "instructions": "You are a helpful assistant.",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": config.PROBE_PROMPT}],
            }
        ],
        "tools": [],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "reasoning": {"effort": config.PROBE_REASONING_EFFORT, "summary": "auto"},
        "store": False,
        "stream": True,
        "include": [],
        "prompt_cache_key": str(uuid.uuid4()),
    }

    request = urllib.request.Request(
        config.RESPONSES_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    context = ssl.create_default_context()

    started = time.time()
    try:
        response = urllib.request.urlopen(
            request, timeout=config.CONNECT_TIMEOUT_SECONDS, context=context
        )
    except urllib.error.HTTPError as exc:
        result.status_code = exc.code
        result.error = f"HTTP {exc.code}"
        if exc.code == 401:
            result.error = "HTTP 401（凭据已失效，请重新同步）"
        _absorb_headers(result, exc.headers)
        return result
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result.error = f"网络错误：{exc.__class__.__name__}"
        return result

    try:
        result.status_code = getattr(response, "status", 200)
        _absorb_headers(result, response.headers)

        if result.status_code != 200:
            result.error = f"HTTP {result.status_code}"
            return result

        buffer = ""
        while result.bytes_read < config.MAX_STREAM_BYTES:
            try:
                chunk = response.read(1024)
            except (TimeoutError, OSError) as exc:
                result.error = f"读取中断：{exc.__class__.__name__}"
                break
            if not chunk:
                break
            result.bytes_read += len(chunk)
            buffer += chunk.decode("utf-8", "replace")

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                served, event_type = _model_from_event(event)
                if served:
                    result.served_model = served
                    result.served_event = event_type
                    break

            # The model is announced in the very first events, so once we have it
            # there is no reason to keep the turn alive and burn quota.
            if result.served_model and result.bytes_read >= config.MIN_BYTES_BEFORE_STOP:
                break

        if not result.served_model and '"type":"error"' in buffer.replace(" ", ""):
            result.error = result.error or "流内返回了 error 事件"
    finally:
        result.elapsed_seconds = round(time.time() - started, 1)
        try:
            response.close()
        except OSError:
            pass

    return result


def _absorb_headers(result: ProbeResult, headers) -> None:
    """Record the server's own signals. Absent headers are simply skipped."""
    if headers is None:
        return
    for name, value in headers.items():
        lowered = name.lower()
        if lowered == "x-codex-turn-state":
            result.turn_state_length = len(value)
        elif lowered == "x-codex-safety-buffering-enabled":
            result.safety_buffering_enabled = value.strip().lower() == "true"
        elif lowered == "x-codex-safety-buffering-faster-model":
            result.safety_faster_model = value
        elif lowered == "x-codex-plan-type":
            result.plan_type = value
        elif lowered == "x-codex-active-limit":
            result.active_limit = value
        elif lowered == "x-codex-primary-used-percent":
            try:
                result.primary_used_percent = int(value)
            except ValueError:
                pass
        elif lowered == "x-codex-primary-window-minutes":
            try:
                result.primary_window_minutes = int(value)
            except ValueError:
                pass
        elif lowered == "x-codex-primary-reset-after-seconds":
            try:
                result.primary_reset_after_seconds = int(value)
            except ValueError:
                pass
        elif lowered in ("x-oai-request-id", "x-request-id"):
            result.request_id = value
        elif lowered == "cf-ray":
            result.cf_ray = value
        elif lowered.startswith(("x-ratelimit", "retry-after")):
            result.extra_headers[lowered] = value


def probe_models(
    models: list[str],
    access_token: str,
    account_id: str,
    user_agent: str = "",
) -> list[ProbeResult]:
    """Probe several models in sequence, returning one result each."""
    return [probe_model(m, access_token, account_id, user_agent) for m in models]
