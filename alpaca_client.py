"""Thin Alpaca connectivity checks (paper account)."""

from __future__ import annotations

import os


def keys_configured() -> bool:
    return bool(os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"))


def alpaca_health() -> dict:
    out = {"connected": False, "keys_configured": keys_configured(), "reason": None}
    if not out["keys_configured"]:
        out["reason"] = "missing .env keys - copy .env.example and add paper keys"
        return out
    try:
        from alpaca.trading.client import TradingClient

        key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_SECRET_KEY")
        trading = TradingClient(key, secret, paper=True)
        trading.get_clock()
        out["connected"] = True
    except Exception as exc:
        out["reason"] = f"Alpaca error: {exc}"
    return out
