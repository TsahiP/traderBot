"""Local LM Studio client (OpenAI-compatible server), shared by the dashboard + analyst bot.

Env: LLM_BASE_URL (default http://127.0.0.1:1234/v1), LLM_MODEL (empty = auto-pick
the best chat model from /v1/models), LLM_IDLE_TTL_S (default 1800). A load sends
that TTL to LM Studio, and every process that chats also unloads the model once
the shared last-use stamp is older than the TTL. Reasoning models may wrap their
thinking in thought tags - those are stripped from every reply.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import config

logger = logging.getLogger("llm_client")

_TAG = "thought"
_THINK_RE = re.compile(rf"<{_TAG}>.*?(?:</{_TAG}>|\Z)", re.DOTALL)

_model_lock = threading.Lock()
_model_cache: str | None = None
_activity_lock = threading.Lock()
_inflight = 0
_watcher_started = False


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


def _activity_path():
    return config.OUTPUT_DIR / "llm_activity.json"


def _touch_activity(model: str) -> None:
    """Shared last-use stamp so dashboard, fundabot and analystbot share one idle clock."""
    path = _activity_path()
    payload = json.dumps({"ts": time.time(), "model": model})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("could not stamp LLM activity: %s", exc)


def _read_activity() -> tuple[float, str] | None:
    try:
        data = json.loads(_activity_path().read_text(encoding="utf-8"))
        return float(data["ts"]), str(data["model"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _instance_ids(model: str) -> list[str]:
    """Loaded native instance ids for this model key. Empty when none, or the list failed."""
    import requests

    base = model.split(":", 1)[0]
    try:
        r = requests.get(f"{_server_root()}/api/v1/models", timeout=10)
        r.raise_for_status()
        rows = r.json().get("models", [])
    except (requests.RequestException, ValueError):
        return []
    ids: list[str] = []
    for row in rows:
        key = str(row.get("key") or "")
        for inst in row.get("loaded_instances") or []:
            iid = str(inst.get("id") or "")
            if not iid:
                continue
            names = {key, iid, key.split("@", 1)[0], iid.split(":", 1)[0]}
            if base in names or model in names:
                ids.append(iid)
    return ids


def unload_model(model: str) -> bool:
    """Ask LM Studio to drop every resident instance of `model`. Best effort."""
    import requests

    ids = _instance_ids(model)
    if not ids:
        return False
    ok = False
    for iid in ids:
        try:
            r = requests.post(
                f"{_server_root()}/api/v1/models/unload",
                json={"instance_id": iid},
                timeout=30,
            )
            ok = ok or r.status_code < 300
        except requests.RequestException as exc:
            logger.warning("unload %s failed: %s", iid, exc)
    if ok:
        logger.info("unloaded %s after %ss with no LLM work", model, config.LLM_IDLE_TTL_S)
    return ok


def unload_if_idle(now: float | None = None) -> bool:
    """Unload when the shared stamp is older than LLM_IDLE_TTL_S and nothing is in flight here."""
    ttl = config.LLM_IDLE_TTL_S
    if ttl <= 0:
        return False
    now = time.time() if now is None else now
    with _activity_lock:
        if _inflight:
            return False
    stamp = _read_activity()
    if stamp is None:
        return False
    ts, model = stamp
    if now - ts < ttl:
        return False
    return unload_model(model)


def _idle_loop() -> None:
    while True:
        time.sleep(30)
        try:
            with _activity_lock:
                busy = _inflight > 0
                model = _read_activity()
            if busy and model is not None:
                _touch_activity(model[1])  # a long call must not look idle to the other processes
                continue
            unload_if_idle()
        except Exception:
            logger.exception("llm idle check failed")


def _start_idle_watcher() -> None:
    global _watcher_started
    if config.LLM_IDLE_TTL_S <= 0:
        return
    with _activity_lock:
        if _watcher_started:
            return
        _watcher_started = True
    threading.Thread(target=_idle_loop, name="llm-idle-ttl", daemon=True).start()


def _begin_work(model: str) -> None:
    global _inflight
    with _activity_lock:
        _inflight += 1
    _touch_activity(model)
    _start_idle_watcher()


def _end_work(model: str) -> None:
    global _inflight
    with _activity_lock:
        _inflight = max(0, _inflight - 1)
    _touch_activity(model)


def ensure_model_loaded(model: str, *, timeout: int | None = None) -> bool:
    """Pre-load a model through the native API, but only when it is not resident.

    Re-loading an already-active model costs ~1-6 minutes of dead GPU time, so we
    check first. The load carries LLM_IDLE_TTL_S so LM Studio itself unloads it
    after that much silence. Best effort in every case: JIT loading on the chat
    endpoint is the fallback, and a hung load check is treated as "already fine".
    """
    import requests

    if timeout is None:
        timeout = config.LLM_LOAD_TIMEOUT
    state = _model_is_loaded(model)
    if state is not False:
        # resident (or the server can't say) - never force a reload of a live model
        return bool(state)
    body: dict = {"model": model}
    if config.LLM_IDLE_TTL_S > 0:
        body["ttl"] = config.LLM_IDLE_TTL_S
    try:
        r = requests.post(
            f"{_server_root()}/api/v1/models/load",
            json=body,
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
    reasoning_effort: str | None = None,
    model: str | None = None,
) -> tuple[str, dict]:
    """One chat round trip -> (final content, meta) with reasoning diagnostics.

    meta keys: finish_reason, completion_tokens, reasoning_tokens, reasoning_len.
    Reasoning models put their thinking in a separate reasoning_content field;
    if the think phase burns the whole token budget the final content stays
    empty and finish_reason is "length" - callers can escalate the budget.
    """
    model = model or resolve_llm_model()
    _begin_work(model)
    try:
        return _llm_chat_ex(
            model, messages,
            temperature=temperature, max_tokens=max_tokens, timeout=timeout,
            enable_thinking=enable_thinking, preload=preload,
            reasoning_effort=reasoning_effort,
        )
    finally:
        _end_work(model)


def _llm_chat_ex(
    model: str,
    messages: list[dict],
    *,
    temperature: float,
    max_tokens: int,
    timeout: int,
    enable_thinking: bool | None,
    preload: bool,
    reasoning_effort: str | None,
) -> tuple[str, dict]:
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
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    raw = _post(url=f"{config.LLM_BASE_URL}/chat/completions", json=body, timeout=timeout)
    if raw is None:  # kwargs rejected -> plain retry
        body.pop("chat_template_kwargs", None)
        body.pop("reasoning_effort", None)
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
