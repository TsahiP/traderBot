"""Ticker normalization and the `-USD` crypto sign.

A symbol that ends with `-USD` (after uppercasing) is spot crypto and is
fetched from Bybit. Everything else stays on the stock data path.
`BTC` and `ETH` alias to `BTC-USD` / `ETH-USD` because bare Yahoo tickers
are Grayscale ETFs, not spot.
"""
import re

_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")
_ALIASES = {"BTC": "BTC-USD", "ETH": "ETH-USD"}


def normalize_symbol(symbol: str) -> str:
    s = str(symbol).strip().upper()
    return _ALIASES.get(s, s)


def is_valid_symbol(symbol: str) -> bool:
    return bool(_SYMBOL_RE.match(normalize_symbol(symbol)))


def is_bybit_crypto(symbol: str) -> bool:
    return normalize_symbol(symbol).endswith("-USD")


def to_bybit_spot_symbol(symbol: str) -> str:
    """`BTC-USD` → spot `BTCUSDT`. Inverse `BTCUSD` is not used."""
    s = normalize_symbol(symbol)
    if not s.endswith("-USD"):
        raise ValueError(f"'{s}' is not a -USD crypto ticker")
    base = s[: -len("-USD")]
    if not base or not base.isalnum():
        raise ValueError(f"Invalid crypto ticker '{s}'")
    return f"{base}USDT"
