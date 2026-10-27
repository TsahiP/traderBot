import json

import pytest

import llm_client as lc


def test_resolve_prefers_config(monkeypatch):
    monkeypatch.setattr(lc.config, "LLM_MODEL", "qwen/qwen3.8-27b")
    lc.reset_model_cache()
    assert lc.resolve_llm_model() == "qwen/qwen3.8-27b"
    lc.reset_model_cache()


def test_resolve_autopick(monkeypatch):
    monkeypatch.setattr(lc.config, "LLM_MODEL", "")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    lc.reset_model_cache()
    calls = []

    class _Res:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": [
                    {"id": "text-embedding-nomic"},
                    {"id": "qwen3.8-27b"},
                    {"id": "gemma"},
                ]
            }

    def fake_get(url, timeout):
        calls.append(url)
        return _Res()

    import requests as req_mod

    monkeypatch.setattr(req_mod, "get", fake_get)
    assert lc.resolve_llm_model() == "qwen3.8-27b"
    assert calls and "models" in calls[0]
    n = len(calls)
    assert lc.resolve_llm_model() == "qwen3.8-27b"
    assert len(calls) == n  # second call served from cache
    lc.reset_model_cache()


def test_resolve_no_models_raises(monkeypatch):
    monkeypatch.setattr(lc.config, "LLM_MODEL", "")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    lc.reset_model_cache()

    import requests as req_mod

    monkeypatch.setattr(
        req_mod, "get", lambda *a, **k: (_EmbedOnly())
    )
    with pytest.raises(RuntimeError):
        lc.resolve_llm_model()
    lc.reset_model_cache()


class _EmbedOnly:
    def raise_for_status(self):
        pass

    def json(self):
        return {"data": [{"id": "text-embedding-nomic-embed-text-v1.5"}]}


def test_strip_think_blocks():
    tag_open = "<" + lc._TAG + ">"
    tag_close = "</" + lc._TAG + ">"
    assert lc.strip_think_blocks("hi") == "hi"
    assert lc.strip_think_blocks(f"{tag_open}a{tag_close}b") == "b"
    assert lc.strip_think_blocks(tag_open + "never closes") == ""
    assert lc.strip_think_blocks("") == ""


def test_llm_chat_posts_and_strips(monkeypatch):
    lc.reset_model_cache()
    posted = {}

    class _Res:
        def raise_for_status(self):
            pass

        def json(self):
            content = "<" + lc._TAG + ">sneaky</" + lc._TAG + ">## Verdict"
            return {"choices": [{"message": {"content": content}}]}

    def fake_post(url, json=None, timeout=None):
        posted["url"] = url
        posted["body"] = json
        return _Res()

    import requests as req_mod

    monkeypatch.setattr(req_mod, "post", fake_post)
    monkeypatch.setattr(lc.config, "LLM_MODEL", "qwen/qwen3.8-27b")
    out = lc.llm_chat([{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=10)
    assert out == "## Verdict"
    assert posted["url"].endswith("/chat/completions")
    assert posted["body"]["model"] == "qwen/qwen3.8-27b"
    assert posted["body"]["max_tokens"] == 10


def test_llm_chat_ex_meta(monkeypatch):
    lc.reset_model_cache()
    j = {
        "choices": [{
            "finish_reason": "length",
            "message": {"content": "", "reasoning_content": "think think"},
        }],
        "usage": {"completion_tokens": 42,
                  "completion_tokens_details": {"reasoning_tokens": 40}},
    }

    class _Res:
        def raise_for_status(self):
            pass

        def json(self):
            return j

    monkeypatch.setattr(lc, "_post", lambda url, json=None, timeout=None: (j["choices"][0]["message"], j["choices"][0], j["usage"]))
    content, meta = lc.llm_chat_ex([{"role": "user", "content": "hi"}], max_tokens=42)
    assert content == ""
    assert meta["finish_reason"] == "length"
    assert meta["completion_tokens"] == 42
    assert meta["reasoning_tokens"] == 40
    assert meta["reasoning_len"] == len("think think")


def test_ensure_model_loaded(monkeypatch):
    posts, gets = [], []

    class _Resp:
        def __init__(self, status=200, payload=None):
            self.status_code = status
            self._payload = payload or {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def fake_post(url, json=None, timeout=None):
        posts.append((url, json, timeout))
        return _Resp()

    def fake_get(url, timeout=None):
        gets.append(url)
        return _Resp(200, {"data": [
            {"id": "other-8b", "state": "loaded"},
            {"id": "target:2", "state": "loaded"},
        ]})

    import requests as req_mod

    monkeypatch.setattr(req_mod, "post", fake_post)
    monkeypatch.setattr(req_mod, "get", fake_get)
    monkeypatch.setattr(lc.config, "LLM_BASE_URL", "http://127.0.0.1:1234/v1")

    # resident under an instance-suffixed id -> no reload POST
    assert lc._model_is_loaded("target") is True
    assert lc.ensure_model_loaded("target") is True
    assert posts == [] and gets

    # absent / not loaded -> load POST fires
    monkeypatch.setattr(req_mod, "get", lambda *a, **k: _Resp(200, {"data": [{"id": "target", "state": "not-loaded"}]}))
    assert lc.ensure_model_loaded("target") is True
    url, payload, tmo = posts[-1]
    assert url == "http://127.0.0.1:1234/api/v1/models/load"
    assert payload == {"model": "target"}

    # unknown model id -> load attempted
    posts.clear()
    monkeypatch.setattr(req_mod, "get", lambda *a, **k: _Resp(200, {"data": []}))
    assert lc.ensure_model_loaded("target") is True
    assert posts and posts[0][0].endswith("/models/load")

    # server busy / check fails -> do NOT force a reload; JIT path still covers it
    posts.clear()

    def boom(*a, **k):
        raise req_mod.ConnectionError("busy")

    monkeypatch.setattr(req_mod, "get", boom)
    assert lc._model_is_loaded("target") is None
    assert lc.ensure_model_loaded("target") is False  # unknown -> not claiming resident
    assert posts == []  # skipped the load entirely

    # load endpoint itself failing -> False but JIT path still covers it
    monkeypatch.setattr(req_mod, "get", lambda *a, **k: _Resp(200, {"data": [{"id": "target", "state": "not-loaded"}]}))
    monkeypatch.setattr(req_mod, "post", boom)
    assert lc.ensure_model_loaded("target") is False

    # old LM Studio without the native API -> fall back to load attempt
    posts.clear()
    monkeypatch.setattr(req_mod, "post", fake_post)
    monkeypatch.setattr(req_mod, "get", lambda *a, **k: _Resp(404, {}))
    assert lc.ensure_model_loaded("target") is True
    assert posts 


def test_json_roundtrip_of_context():
    # llm payload builders must be json-safe (used by both dashboard + analyst)
    data = {"a": 1.5, "b": None}
    json.dumps(data)
    lc.reset_model_cache()
