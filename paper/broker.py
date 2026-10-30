"""Alpaca adapter for paperbot: the only module in paper/ that imports alpaca-py.

Everything it returns is a plain dict (see `normalize_order` /
`normalize_position`), so the funnel, executor and reconcile code - and their
tests, which use tests/paper_fakes.FakeBroker - never touch Alpaca objects.
The trading client is always the paper one (`paper=True` is not a setting).

Order shapes worth knowing (`exit_legs` reads both):
- bracket: the parent is the entry; its legs are the take-profit (limit) and
  the stop-loss (stop), held until the entry fills;
- OCO: the parent IS the take-profit (limit) and its one leg is the stop.
Prices go out rounded to the cent; brackets are whole shares only.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

import config

logger = logging.getLogger("paperbot")
NY = ZoneInfo("America/New_York")

OPEN_STATUSES = {"new", "accepted", "pending_new", "accepted_for_bidding", "partially_filled",
                 "held", "pending_replace", "pending_cancel"}
DEAD_STATUSES = {"canceled", "expired", "rejected", "done_for_day", "stopped", "suspended", "replaced"}
SIDES = ("buy", "sell")
TIFS = ("day", "gtc")
BAR_COLUMNS = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}


class BrokerError(RuntimeError):
    """Setup problems (missing keys). API errors propagate as Alpaca raises them."""


def _val(x) -> str:
    """Enum or plain value -> lowercase string (alpaca-py returns enums)."""
    return str(getattr(x, "value", x) or "").lower()


def _num(x) -> float | None:
    return None if x is None or x == "" else float(x)


def _iso(ts) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    return ts.astimezone(NY).isoformat(timespec="seconds")


def normalize_order(o) -> dict:
    return {
        "id": str(o.id),
        "client_order_id": getattr(o, "client_order_id", None),
        "symbol": o.symbol,
        "side": _val(o.side),
        "qty": _num(o.qty),
        "filled_qty": _num(o.filled_qty) or 0.0,
        "filled_avg": _num(o.filled_avg_price),
        "type": _val(getattr(o, "type", None) or getattr(o, "order_type", None)),
        "order_class": _val(o.order_class) or "simple",
        "tif": _val(o.time_in_force),
        "status": _val(o.status),
        "limit_price": _num(o.limit_price),
        "stop_price": _num(o.stop_price),
        "submitted_at": _iso(getattr(o, "submitted_at", None)),
        "legs": [normalize_order(leg) for leg in (o.legs or [])],
    }


def normalize_position(p) -> dict:
    return {
        "symbol": p.symbol,
        "side": _val(p.side),
        "qty": abs(float(p.qty)),
        "avg_entry": float(p.avg_entry_price),
        "current_price": _num(p.current_price),
        "market_value": _num(p.market_value),
        "unrealized_pl": _num(p.unrealized_pl),
    }


def exit_legs(order: dict) -> dict:
    """{'stop': order | None, 'target': order | None} of a bracket or an OCO."""
    out = {"stop": None, "target": None}
    candidates = list(order.get("legs") or [])
    if order.get("order_class") == "oco":
        candidates.append(order)
    elif order.get("order_class") != "bracket":
        return out
    for leg in candidates:
        if leg["type"] == "limit":
            out["target"] = leg
        elif leg["type"] in ("stop", "stop_limit"):
            out["stop"] = leg
    return out


def bars_frame(raw: pd.DataFrame, symbol: str, now: datetime) -> pd.DataFrame:
    """Alpaca BarSet.df -> title-case OHLCV on a NY index, today's bar in
    progress dropped (decisions only ever read completed bars)."""
    df = raw
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level="symbol")
    df = df.rename(columns=BAR_COLUMNS)[list(BAR_COLUMNS.values())]
    idx = pd.DatetimeIndex(df.index)
    df.index = idx.tz_localize("UTC").tz_convert(NY) if idx.tz is None else idx.tz_convert(NY)
    if len(df) and df.index[-1].date() == now.date():
        df = df.iloc[:-1]
    return df


def _check_order(side: str, qty) -> None:
    if side not in SIDES:
        raise ValueError(f"side must be buy or sell, got {side!r}")
    if not qty or qty <= 0 or int(qty) != qty:
        raise ValueError(f"qty must be a positive whole number of shares, got {qty!r}")


def _tif(tif: str):
    from alpaca.trading.enums import TimeInForce

    if tif not in TIFS:
        raise ValueError(f"time in force must be day or gtc, got {tif!r}")
    return TimeInForce.GTC if tif == "gtc" else TimeInForce.DAY


def _side(side: str):
    from alpaca.trading.enums import OrderSide

    return OrderSide.BUY if side == "buy" else OrderSide.SELL


def _trading_client_cls():
    from alpaca.trading.client import TradingClient

    return TradingClient


def _data_client(key: str, secret: str):
    from alpaca.data.historical import StockHistoricalDataClient

    return StockHistoricalDataClient(key, secret)


class Broker:
    def __init__(self, trading, data):
        self.trading = trading
        self.data = data

    @classmethod
    def from_env(cls) -> "Broker":
        load_dotenv(config.BASE_DIR / ".env")
        key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise BrokerError("Missing ALPACA_API_KEY / ALPACA_SECRET_KEY in .env - add your paper keys.")
        return cls(_trading_client_cls()(key, secret, paper=True), _data_client(key, secret))

    # ------------------------------------------------------------ reads

    def clock(self) -> dict:
        c = self.trading.get_clock()
        return {"is_open": bool(c.is_open), "timestamp": _iso(c.timestamp),
                "next_open": _iso(c.next_open), "next_close": _iso(c.next_close)}

    def account(self) -> dict:
        a = self.trading.get_account()
        return {"equity": float(a.equity), "cash": float(a.cash),
                "buying_power": float(a.buying_power),
                "shorting_enabled": bool(getattr(a, "shorting_enabled", False))}

    def positions(self) -> list[dict]:
        return [normalize_position(p) for p in self.trading.get_all_positions()]

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        """Every open order, legs included, as a flat list."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=False,
                               symbols=[symbol] if symbol else None)
        return [normalize_order(o) for o in self.trading.get_orders(filter=req)]

    def order(self, order_id: str) -> dict:
        from alpaca.trading.requests import GetOrderByIdRequest

        return normalize_order(self.trading.get_order_by_id(order_id, filter=GetOrderByIdRequest(nested=True)))

    def filled_today(self, symbol: str, side: str, now: datetime) -> bool:
        """Did an order on `side` (buy / sell) for `symbol` fill today (NY)?
        Tells an adopted position opened today (flattened at flat-by) from an older one."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        midnight = now.astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0)
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[symbol], after=midnight, nested=False)
        return any(_val(o.side) == side and float(o.filled_qty or 0) > 0
                   for o in self.trading.get_orders(filter=req))

    def asset(self, symbol: str) -> dict:
        a = self.trading.get_asset(symbol)
        return {"symbol": a.symbol, "tradable": bool(a.tradable), "shortable": bool(a.shortable),
                "easy_to_borrow": bool(a.easy_to_borrow)}

    def latest_price(self, symbol: str) -> float | None:
        """Last IEX trade (free feed); None when it is not available."""
        try:
            from alpaca.data.enums import DataFeed
            from alpaca.data.requests import StockLatestTradeRequest

            req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
            trade = self.data.get_stock_latest_trade(req)[symbol]
            return float(trade.price) if trade.price and trade.price > 0 else None
        except Exception as exc:
            logger.warning("Latest trade for %s unavailable: %s", symbol, exc)
            return None

    def daily_bars(self, symbol: str, now: datetime, days: int = 400) -> pd.DataFrame:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                               start=now - timedelta(days=days),
                               end=now - timedelta(minutes=16),  # free plan: no SIP for the last 15 min
                               limit=config.ALPACA_BARS_LIMIT)
        return bars_frame(self.data.get_stock_bars(req).df, symbol, now)

    # ------------------------------------------------------------ orders

    def submit_bracket(self, symbol: str, side: str, qty: int, stop: float, target: float,
                       tif: str = "gtc", client_order_id: str | None = None) -> dict:
        """Market entry + stop-loss + take-profit held at Alpaca."""
        from alpaca.trading.enums import OrderClass
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

        _check_order(side, qty)
        req = MarketOrderRequest(
            symbol=symbol, qty=int(qty), side=_side(side), time_in_force=_tif(tif),
            order_class=OrderClass.BRACKET, client_order_id=client_order_id,
            stop_loss=StopLossRequest(stop_price=round(stop, 2)),
            take_profit=TakeProfitRequest(limit_price=round(target, 2)),
        )
        return normalize_order(self.trading.submit_order(req))

    def submit_oco(self, symbol: str, exit_side: str, qty: float, stop: float, target: float) -> dict:
        """Stop + target on a position that is already open (GTC)."""
        from alpaca.trading.enums import OrderClass
        from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, TakeProfitRequest

        _check_order(exit_side, qty)
        req = LimitOrderRequest(
            symbol=symbol, qty=int(qty), side=_side(exit_side), time_in_force=_tif("gtc"),
            order_class=OrderClass.OCO,
            stop_loss=StopLossRequest(stop_price=round(stop, 2)),
            take_profit=TakeProfitRequest(limit_price=round(target, 2)),
        )
        return normalize_order(self.trading.submit_order(req))

    def submit_market(self, symbol: str, side: str, qty: float, tif: str = "day") -> dict:
        from alpaca.trading.requests import MarketOrderRequest

        if side not in SIDES:
            raise ValueError(f"side must be buy or sell, got {side!r}")
        if not qty or qty <= 0:
            raise ValueError(f"qty must be positive, got {qty!r}")
        req = MarketOrderRequest(symbol=symbol, qty=qty, side=_side(side), time_in_force=_tif(tif))
        return normalize_order(self.trading.submit_order(req))

    def cancel(self, order_id: str) -> None:
        self.trading.cancel_order_by_id(order_id)
