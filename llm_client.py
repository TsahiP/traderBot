"""Local LM Studio client (OpenAI-compatible server), shared by the dashboard + analyst bot.

Env: LLM_BASE_URL (default http://127.0.0.1:1234/v1), LLM_MODEL (empty = auto-pick
the best chat model from /v1/models). Reasoning models may wrap their thinking in
thought tags - those are stripped from every reply.
"""
from __future__ import annotations

import os
import re
import threading

import config

_TAG = "thought"
_THINK_RE = re.compile(rf"<{_TAG}>.*?(?:</{_TAG}>|\Z)", re.DOTALL)

_model_lock = threading.Lock()
_model_cache: str | None = None


def reset_model_cache() -> None:
    """Forget the cached model id (call after changing config.LLM_MODEL)."""
    global _model_cache
    with _model_lock:
        _model_cache = None


def resolve_llm_model() -> str:
    """Pick the chat model: configured one first, then heuristic auto-pick."""
    global _model_cache
    if _model_cache:
        return _model_cache
    wanted = config.LLM_MODEL or os.environ.get("LLM_MODEL", "")
    if wanted:
        with _model_lock:
            _model_cache = wanted
        return _model_cache
    import requests

    r = requests.get(f"{config.LLM_BASE_URL}/models", timeout=10)
    r.raise_for_status()
    ids = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
    chat_ids = [m for m in ids if "embed" not in m.lower()]
    qwen_chat = [m for m in chat_ids if "qwen" in m.lower() and "vl" not in m.lower()]
    pick = (
        next((m for m in chat_ids if "hermes" in m.lower()), None)
        or next((m for m in qwen_chat if "qwen3" in m.lower()), None)
        or (qwen_chat[0] if qwen_chat else None)
        or (chat_ids[0] if chat_ids else None)
    )
    if not pick:
        raise RuntimeError("no chat models found in LM Studio")
    with _model_lock:
        _model_cache = pick
    return pick


def strip_think_blocks(text: str) -> str:
    """Remove reasoning-model think blocks (closed or truncated)."""
    if not text:
        return ""
    text = _THINK_RE.sub("", text)
    opener = "<" + _TAG + ">"
    if opener in text:  # unclosed block without an opener: keep what came before
        text = text.split(opener)[0]
    return text.strip()


def vision_user_message(text: str, images: list[tuple[str, bytes]] | None = None) -> dict:
    """OpenAI-compatible user message; multimodal parts when PNG bytes are given.

    images: ordered [(label, png_bytes), ...]. Labels ride along as text parts
    so the model knows which chart it is looking at.
    """
    if not images:
        return {"role": "user", "content": text}
    import base64

    parts: list[dict] = [{"type": "text", "text": text}]
    for label, blob in images:
        b64 = base64.b64encode(blob).decode("ascii")
        parts.append({"type": "text", "text": f"[attached chart: {label}]"})
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
    return {"role": "user", "content": parts}


def _server_root() -> str:
    """LM Studio native-API root (the OpenAI base url minus any /v1)."""
    return config.LLM_BASE_URL.rsplit("/v1", 1)[0].rstrip("/")


def _model_is_loaded(model: str) -> bool | None:
    """Ask LM Studio's native API whether any instance of the model is resident.

    Returns True/False for a definitive answer, or None when the server can't
    tell reliably right now (busy loading / old version) - in that case the
    caller should NOT force a reload (the JIT path covers everything anyway).
    """
    import requests

    try:
        r = requests.get(f"{_server_root()}/api/v0/models", timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
    except (requests.RequestException, ValueError):
        return None
    base = model.split(":", 1)[0]
    for row in data:
        mid = str(row.get("id") or "")
        if mid.split(":", 1)[0] == base:
            if row.get("state") == "loaded":
                return True
            # row exists but not resident
            return False
    return False  # model unknown to the server - safe to attempt a load


def ensure_model_loaded(model: str, *, timeout: int | None = None) -> bool:
    """Pre-load a model through the native API, but only when it is not resident.

    Re-loading an already-active model costs ~1-6 minutes of dead GPU time, so we
    check first. Best effort in every case: JIT loading on the chat endpoint is
    the fallback, and a hung load check is treated as "already fine".
    """
    import requests

    if timeout is None:
        timeout = config.LLM_LOAD_TIMEOUT
    state = _model_is_loaded(model)
    if state is not False:
        # resident (or the server can't say) - never force a reload of a live model
        return bool(state)
    try:
        r = requests.post(
            f"{_server_root()}/api/v1/models/load",
            json={"model": model},
            timeout=timeout,
        )
        return r.status_code < 300
    except requests.RequestException:
        return False


def llm_chat_ex(
    messages: list[dict],
    *,
    temperature: float = 0.4,
    max_tokens: int = 800,
    timeout: int = 600,
    enable_thinking: bool | None = None,
    preload: bool = False,
) -> tuple[str, dict]:
    """One chat round trip -> (final content, meta) with reasoning diagnostics.

    meta keys: finish_reason, completion_tokens, reasoning_tokens, reasoning_len.
    Reasoning models put their thinking in a separate reasoning_content field;
    if the think phase burns the whole token budget the final content stays
    empty and finish_reason is "length" - callers can escalate the budget.
    """
    model = resolve_llm_model()
    if preload:
        ensure_model_loaded(model)
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if enable_thinking is not True:  # default (None) and explicit False both disable
        body["chat_template_kwargs"] = {"enable_thinking": False}
    raw = _post(url=f"{config.LLM_BASE_URL}/chat/completions", json=body, timeout=timeout)
    if raw is None:  # kwargs rejected -> plain retry
        body.pop("chat_template_kwargs", None)
        raw = _post(url=f"{config.LLM_BASE_URL}/chat/completions", json=body, timeout=timeout)
        if raw is None:
            raise RuntimeError("LLM server rejected the chat/completions request")
    msg, choice, usage = raw
    usage = usage or {}
    det = usage.get("completion_tokens_details") or {}
    meta = {
        "finish_reason": choice.get("finish_reason"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": det.get("reasoning_tokens"),
        "reasoning_len": len(msg.get("reasoning_content") or ""),
    }
    # servers that separate reasoning put it there - use the final content only
    content = msg.get("content") or ""
    return strip_think_blocks(content), meta


def llm_chat(
    messages: list[dict],
    *,
    temperature: float = 0.4,
    max_tokens: int = 800,
    timeout: int = 600,
    enable_thinking: bool | None = None,
    preload: bool = False,
) -> str:
    """One chat round trip against the local LM Studio server (content only)."""
    content, _meta = llm_chat_ex(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        enable_thinking=enable_thinking,
        preload=preload,
    )
    return content


def _post(url: str, json: dict, timeout: int):
    import requests

    try:
        r = requests.post(url, json=json, timeout=timeout)
        r.raise_for_status()
    except (requests.RequestException, ValueError):
        return None
    try:
        choice = r.json()["choices"][0]
        return choice.get("message") or {}, choice, r.json().get("usage") or {}
    except (KeyError, IndexError, ValueError):
        return None
