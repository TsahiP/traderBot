"""Symbol conventions for market-data routing (crypto vs equities)."""

from __future__ import annotations

import re

_CRYPTO_SUFFIX = "-USD"
_BYBIT_BASE_RE = re.compile(r"^[A-Z0-9]{1,10}$")


def is_crypto_symbol(symbol: str) -> bool:
    """True when OHLCV should come from Bybit spot (e.g. BTC-USD)."""
    return symbol.strip().upper().endswith(_CRYPTO_SUFFIX)


def to_bybit_spot_symbol(symbol: str) -> str:
    """Map a canonical crypto ticker to Bybit spot symbol (USDT quote).

    BTC-USD -> BTCUSDT
    """
    sym = symbol.strip().upper()
    if not sym.endswith(_CRYPTO_SUFFIX):
        raise ValueError(f"Not a -USD crypto symbol: '{symbol}'")
    base = sym[: -len(_CRYPTO_SUFFIX)]
    if not base or not _BYBIT_BASE_RE.match(base):
        raise ValueError(f"Invalid crypto base in '{symbol}'")
    return f"{base}USDT"
