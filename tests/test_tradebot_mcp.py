"""MCP server: tool registry and a few offline tool calls through the protocol layer."""
import asyncio
import json

import pytest

import tradebot_mcp


def _call(name, args):
    result = asyncio.run(tradebot_mcp.server.call_tool(name, args))
    assert not getattr(result, "is_error", False), result
    if getattr(result, "structured_content", None) is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


def test_tools_registered_and_read_only():
    names = {t.name for t in asyncio.run(tradebot_mcp.server.list_tools())}
    assert names == {
        "analyze_symbol", "run_backtest", "time_machine", "scan_market", "last_scan",
        "fundamentals", "news", "signal_history", "signal_stats", "risk_check", "levels",
        "market_status",
    }
    assert not any("order" in n or "place" in n for n in names)


def test_market_status_tool():
    out = _call("market_status", {"date": "2026-11-27"})
    assert out["trading_day"] and out["early_close"] and out["close"] == "13:00"
    assert _call("market_status", {"date": "2026-11-26"})["holiday"] is True


def test_risk_check_sizes_and_gates():
    out = _call("risk_check", {"symbol": "spy", "entry": 100.0, "stop": 95.0, "target": 110.0,
                               "equity": 100_000.0})
    assert out["qty"] == 40 and out["size_limited_by"] == "max_loss"   # $200 / $5
    assert out["max_loss_usd"] == 200.0
    bad = _call("risk_check", {"symbol": "SPY", "entry": 100.0, "stop": 101.0, "qty": 10})
    assert bad["ok"] is False and any(r.startswith("stop:") for r in bad["reasons"])


def test_last_scan_reads_results(monkeypatch, tmp_path):
    import tools.scanner as scanner

    path = tmp_path / "scan.json"
    path.write_text(json.dumps({"mode": "daily", "hits": [{"symbol": "KO"}]}), encoding="utf-8")
    monkeypatch.setattr(scanner, "RESULTS_PATH", path)
    assert _call("last_scan", {})["hits"] == [{"symbol": "KO"}]


def test_bad_symbol_is_a_tool_error():
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(Exception) as exc:
        asyncio.run(tradebot_mcp.server.call_tool("time_machine", {
            "symbol": "BTC-USD", "entry": 1.0, "stop": 0.9, "target": 1.2}))
    assert "stocks only" in str(exc.value) or isinstance(exc.value, ToolError)
