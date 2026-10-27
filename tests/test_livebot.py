"""livebot cycle against a fake Alpaca client: bracket entry, broker-side exits,
restart safety, API errors and the risk gate."""
from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

import config
import livebot

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 27, 10, 30, tzinfo=NY)


class FakeTrading:
    def __init__(self, fill=120.0):
        self.is_open = True
        self.positions = []
        self.orders = {}
        self.open_orders = []
        self.submitted = []
        self.canceled = []
        self.fill = fill
        self.equity = "100000"
        self.positions_error = None

    def get_clock(self):
        return NS(is_open=self.is_open)

    def get_all_positions(self):
        if self.positions_error:
            raise self.positions_error
        return list(self.positions)

    def get_account(self):
        return NS(equity=self.equity, cash=self.equity)

    def submit_order(self, req):
        oid = f"o{len(self.submitted) + 1}"
        self.submitted.append(req)
        self.orders[oid] = NS(id=oid, filled_avg_price=self.fill, status="filled",
                              filled_qty=req.qty, legs=[])
        return NS(id=oid)

    def get_order_by_id(self, oid, filter=None):
        return self.orders[oid]

    def get_orders(self, filter=None):
        return list(self.open_orders)

    def cancel_order_by_id(self, oid):
        self.canceled.append(oid)
        self.open_orders = [o for o in self.open_orders if o.id != oid]


def _bars(direction: str) -> pd.DataFrame:
    """80 daily bars whose SMA 10/50 crosses on the last bars (up or down)."""
    n = 80
    if direction == "up":
        close = np.r_[np.linspace(110, 100, 70), np.linspace(101, 120, 10)]
    else:
        close = np.r_[np.linspace(100, 110, 70), np.linspace(109, 90, 10)]
    idx = pd.date_range("2026-06-01", periods=n, freq="B", tz=NY)
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                         "Close": close, "Volume": 1e6}, index=idx)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(livebot, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(livebot, "TRADES_PATH", tmp_path / "live_trades.csv")
    monkeypatch.setattr(livebot, "_sleep", lambda s: None)
    monkeypatch.setattr(livebot, "latest_price", lambda data, fallback: fallback)
    holder = {"bars": _bars("up")}
    monkeypatch.setattr(livebot, "get_last_completed_bars", lambda data, now: holder["bars"])
    return holder, tmp_path


def _trades(tmp_path) -> pd.DataFrame:
    return pd.read_csv(tmp_path / "live_trades.csv")


def test_bars_from_alpaca_title_cases_and_drops_today():
    ts = pd.to_datetime(["2026-10-23 04:00", "2026-10-26 04:00", "2026-10-27 04:00"], utc=True)
    raw = pd.DataFrame(
        {"symbol": config.SYMBOL, "timestamp": ts, "open": [1.0, 2, 3], "high": [1.0, 2, 3],
         "low": [1.0, 2, 3], "close": [1.0, 2, 3], "volume": [10.0, 20, 30],
         "trade_count": 1, "vwap": 1.0}
    ).set_index(["symbol", "timestamp"])
    df = livebot.bars_from_alpaca(raw, NOW)
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 2 and df.index[-1].date().isoformat() == "2026-10-26"
    assert str(df.index.tz) == "America/New_York"


def test_buy_places_bracket_and_persists_state(env):
    holder, tmp = env
    trading = FakeTrading()
    state = livebot.load_state()
    assert livebot.run_cycle(trading, None, state, NOW) == "entered"

    (req,) = trading.submitted
    assert livebot._val(req.order_class) == "bracket"
    assert livebot._val(req.time_in_force) == "gtc"
    assert req.stop_loss.stop_price < 120.0 < req.take_profit.limit_price
    risk_ps = 120.0 - req.stop_loss.stop_price
    assert req.take_profit.limit_price == pytest.approx(120.0 + config.TARGET_R * risk_ps, abs=0.011)

    saved = livebot.load_state()
    assert saved["open_trade"]["order_id"] == "o1"
    assert saved["entries"][0]["max_loss"] == pytest.approx(config.QUANTITY * risk_ps, abs=0.01)
    assert saved["acted_signal_bar"]


def test_stop_leg_fill_is_recorded_and_crossover_not_rebought(env):
    holder, tmp = env
    trading = FakeTrading()
    state = livebot.load_state()
    livebot.run_cycle(trading, None, state, NOW)
    stop = trading.submitted[0].stop_loss.stop_price

    # broker filled the stop leg: no position any more
    trading.orders["o1"].legs = [
        NS(status="filled", type="stop", filled_avg_price=stop),
        NS(status="canceled", type="limit", filled_avg_price=None),
    ]
    status = livebot.run_cycle(trading, None, state, NOW.replace(hour=11))
    assert status == "already_acted"
    assert len(trading.submitted) == 1          # same crossover is not bought again
    row = _trades(tmp).iloc[-1]
    assert row["exit_price"] == pytest.approx(stop)
    assert row["pnl"] == pytest.approx((stop - 120.0) * config.QUANTITY, abs=0.01)
    assert state["open_trade"] is None


def test_restart_with_position_sells_using_broker_entry_price(env):
    holder, tmp = env
    holder["bars"] = _bars("down")
    trading = FakeTrading(fill=91.0)
    trading.positions = [NS(symbol=config.SYMBOL, qty="10", avg_entry_price="105.5")]
    trading.open_orders = [NS(id="leg-stop", type="stop"), NS(id="leg-tp", type="limit")]
    state = livebot.load_state()                 # empty: the bot lost its memory

    assert livebot.run_cycle(trading, None, state, NOW) == "exited"
    assert trading.canceled == ["leg-stop", "leg-tp"]
    sell = trading.submitted[-1]
    assert livebot._val(sell.side) == "sell" and float(sell.qty) == 10
    row = _trades(tmp).iloc[-1]
    assert row["entry_price"] == 105.5 and row["exit_price"] == 91.0
    assert row["pnl"] == pytest.approx((91.0 - 105.5) * 10)


def test_positions_api_error_aborts_cycle_without_ordering(env):
    trading = FakeTrading()
    trading.positions_error = ConnectionError("network blip")
    with pytest.raises(ConnectionError):
        livebot.run_cycle(trading, None, livebot.load_state(), NOW)
    assert trading.submitted == []


def test_risk_block_places_nothing(env, monkeypatch):
    monkeypatch.setattr(config, "RISK_SIZE_BY_RISK", False)   # fixed qty -> the gate must block
    trading = FakeTrading()
    trading.equity = "3000"                      # position cap + cash buffer fail
    state = livebot.load_state()
    assert livebot.run_cycle(trading, None, state, NOW) == "blocked"
    assert trading.submitted == []
    assert state["last_block"].startswith("BLOCK:") and "position:" in state["last_block"]
    assert state["acted_signal_bar"] is None     # may still enter once the block clears


def test_entry_window_blocks_early_buy(env):
    trading = FakeTrading()
    state = livebot.load_state()
    assert livebot.run_cycle(trading, None, state, NOW.replace(hour=9, minute=35)) == "blocked"
    assert "window:" in state["last_block"]


def test_unfilled_entry_is_cleared(env):
    trading = FakeTrading()
    state = livebot.load_state()
    livebot.run_cycle(trading, None, state, NOW)
    trading.orders["o1"].status = "canceled"
    trading.orders["o1"].filled_qty = "0"
    livebot.reconcile(trading, state, None, 120.0, NOW)
    assert state["open_trade"] is None
    assert not (env[1] / "live_trades.csv").exists()


def test_market_closed_does_nothing(env):
    trading = FakeTrading()
    trading.is_open = False
    assert livebot.run_cycle(trading, None, livebot.load_state(), NOW) == "closed"
    assert trading.submitted == []


def test_early_close_shortens_entry_window():
    assert livebot.risk_limits(datetime(2026, 11, 27, 11, 0, tzinfo=NY)).no_entries_after == "12:30"
    assert livebot.risk_limits(NOW).no_entries_after == config.RISK_NO_ENTRIES_AFTER


def test_size_by_risk_buys_fewer_shares(env, monkeypatch):
    monkeypatch.setattr(config, "RISK_MAX_LOSS_PER_TRADE_USD", 20.0)
    monkeypatch.setattr(config, "RISK_SIZE_BY_RISK", True)
    trading = FakeTrading()
    state = livebot.load_state()
    assert livebot.run_cycle(trading, None, state, NOW) == "entered"
    req = trading.submitted[0]
    risk_ps = 120.0 - req.stop_loss.stop_price
    assert 0 < float(req.qty) < config.QUANTITY
    assert float(req.qty) * risk_ps <= 20.0 + 1e-6
    assert state["open_trade"]["qty"] == float(req.qty)


def test_size_zero_blocks_with_reason(env, monkeypatch):
    monkeypatch.setattr(config, "RISK_MAX_LOSS_PER_TRADE_USD", 0.5)
    trading = FakeTrading()
    state = livebot.load_state()
    assert livebot.run_cycle(trading, None, state, NOW) == "blocked"
    assert state["last_block"].startswith("BLOCK: size: 0 shares fit (limited by max_loss)")
