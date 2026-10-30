"""The paperbot loop: one `tick(now)` at a time, fully driven by the clock it is given.

Each tick:
1. drains the inbox - stream items (order updates, quotes, stream status) and
   UI commands - so a fill or a click is handled within a second;
2. re-reads the config file when it changed (`bot.config`);
3. runs the jobs that are due on the NYSE calendar:
   - queued swing entries at the entry window (re-checked by the executor);
   - flat-by exits for trades that carry a flat-by time;
   - proposal expiry; a minute equity snapshot while the market is open;
   - the swing pass after the close, once a day: each swing run evaluates
     its symbols on the completed daily bar; exits go out at once (Alpaca
     holds them for the open), entries are decided now and queued for the
     next session's window (or proposed, with the approval switch on);
4. keeps the quote stream on the symbols that matter, writes the heartbeat
   and caches the `/state` snapshot the HTTP server serves.

Pause blocks new entries and proposals only; exits and commands still run.
A queued entry that meets a paused bot or a passed window does not fire
late - it is blocked or expired with the reason.
"""
from __future__ import annotations

import logging
import os
import queue
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import market_calendar
import risk
from paper import config as pcfg
from paper.broker import OPEN_STATUSES
from paper.executor import Executor, ExecutorError, record_order
from paper.funnel import FunnelContext, run_funnel
from paper.reconcile import reconcile
from paper.runs import IllegalTransition, Intent, Run, Setup, SetupState as S, evaluate_daily, runs_from_config, transition

logger = logging.getLogger("paperbot")
NY = ZoneInfo("America/New_York")

SWING_PASS_DELAY = timedelta(minutes=20)    # after the close: the daily bar is final on the free 15-min-delayed feed
BROKER_REFRESH_S = 15.0                     # account / positions cache
EXIT_REASON_BY_TYPE = {"limit": "target", "stop": "stop", "stop_limit": "stop"}
COMMANDS = ("pause", "resume", "flatten_all", "close_position", "set_approval", "approve", "reject")


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def at_hhmm(day: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=NY)


def next_session_day(day: date) -> date:
    d = day + timedelta(days=1)
    while market_calendar.session(d) is None:
        d += timedelta(days=1)
    return d


class Bot:
    def __init__(self, broker, journal, bus, config_path: Path, *, executor: Executor | None = None,
                 quotes=None, streams: dict | None = None, pid: int | None = None):
        self.broker = broker
        self.journal = journal
        self.bus = bus
        self.executor = executor or Executor(broker, journal, bus)
        self.quotes = quotes                    # QuoteStreamThread-like: set_symbols(), health()
        self.streams = dict(streams or {})      # name -> object with health()
        self.inbox: queue.Queue = queue.Queue()
        self._early: list[dict] = []           # items `wait` already took off the inbox
        self.config_path = Path(config_path)
        self.pid = pid or os.getpid()
        self.cfg, error = pcfg.load_config(self.config_path)
        self.cfg_hash = pcfg.config_hash(self.cfg)
        self._cfg_mtime = self._mtime()
        heartbeat = journal.read_heartbeat()
        self.paused = bool(heartbeat and heartbeat["paused"])
        self.prices: dict[str, float] = {}
        self.market = "closed"
        self.next_wake: str | None = None
        self.snapshot: dict = {}
        self._extremes: dict[int, list[float]] = {}
        self._account: dict | None = None
        self._positions: list | None = None
        self._broker_at: datetime | None = None
        self._broker_dirty = True
        self._equity_minute: str | None = None
        self._assets: dict[tuple[str, str], dict] = {}
        self._stream_state: dict[str, str] = {}
        if error:
            self.bus.publish("error", {"message": f"config: {error}"})

    # ------------------------------------------------------------ lifecycle

    def start(self, now: datetime) -> None:
        report = reconcile(self.broker, self.journal, self.bus, now=now,
                           flat_by=self.cfg["global_risk"]["flat_by"])
        self.bus.publish("bot.start", {"pid": self.pid, "config_hash": self.cfg_hash, "paused": self.paused,
                                       "reconcile": asdict(report)})
        self._broker_dirty = True
        self.tick(now)

    def post(self, item: dict) -> None:
        """Thread-safe: streams and the HTTP server hand work to the loop here."""
        self.inbox.put(item)

    def wait(self, timeout: float) -> None:
        """Block until work arrives or `timeout` passes (the main loop's sleep)."""
        try:
            self._early.append(self.inbox.get(timeout=timeout))
        except queue.Empty:
            pass

    def tick(self, now: datetime) -> None:
        self._drain(now)
        self._reload_config()
        session = market_calendar.session(now.date())
        self.market = "open" if session and session[0] <= now < session[1] else "closed"
        if session:
            self._jobs(now, session)
        self._expire_proposals(now)
        self._refresh_broker(now)
        self._update_quote_symbols()
        self.next_wake = self._next_wake(now)
        self._heartbeat(now)
        self.snapshot = self._build_snapshot(now)

    # ------------------------------------------------------------ config

    def _mtime(self) -> float | None:
        try:
            return self.config_path.stat().st_mtime
        except OSError:
            return None

    def _reload_config(self, force: bool = False) -> None:
        mtime = self._mtime()
        if not force and mtime == self._cfg_mtime:
            return
        self._cfg_mtime = mtime
        cfg, error = pcfg.load_config(self.config_path, last_good=self.cfg)
        if error:
            self.bus.publish("error", {"message": f"config: {error} - keeping the last good config"})
            return
        new_hash = pcfg.config_hash(cfg)
        if new_hash != self.cfg_hash:
            self.cfg, self.cfg_hash = cfg, new_hash
            self.bus.publish("bot.config", {"config_hash": new_hash, "approval_mode": cfg["approval_mode"],
                                            "llm_mode": cfg["llm_mode"]})

    def _run(self, run_id: str) -> Run | None:
        return next((r for r in runs_from_config(self.cfg) if r.id == run_id), None)

    # ------------------------------------------------------------ setups

    def _set_setup(self, run_id: str, symbol: str, day: str, state: S, now: datetime, **changes) -> None:
        row = self.journal.get_setup(run_id, symbol, day)
        if row is None:
            setup = Setup(run_id=run_id, symbol=symbol, day=day, state=state, **changes)
        else:
            try:
                setup = transition(Setup.from_row(row), state, **changes)
            except IllegalTransition as exc:
                logger.warning("setup %s", exc)
                return
        self._store_setup(setup, now)

    def _move_latest(self, run_id: str, symbol: str, state: S, now: datetime, **changes) -> None:
        """Move the most recent setup of a run/symbol (a swing setup spans days)."""
        row = self.journal.latest_setup(run_id, symbol)
        if row is None:
            return
        try:
            setup = transition(Setup.from_row(row), state, **changes)
        except IllegalTransition as exc:
            logger.info("setup %s - left as is", exc)
            return
        self._store_setup(setup, now)

    def _store_setup(self, setup: Setup, now: datetime) -> None:
        row = setup.as_row()
        self.journal.upsert_setup(**row, ts=_iso(now))
        self.bus.publish("setup.state", row, run_id=setup.run_id, symbol=setup.symbol)

    # ------------------------------------------------------------ context

    def _asset(self, symbol: str, now: datetime) -> dict:
        key = (symbol, now.date().isoformat())
        if key not in self._assets:
            self._assets[key] = self.broker.asset(symbol)
        return self._assets[key]

    def _pending_symbols(self) -> set[str]:
        pending = {o["symbol"] for o in self.journal.orders(statuses=tuple(OPEN_STATUSES)) if o["kind"] == "entry"}
        pending |= {p["symbol"] for p in self.executor.pending_proposals()}
        pending |= {s["symbol"] for s in self.journal.setups_in_state(S.TRIGGERED.value)
                    if (s["detail"] or {}).get("queued")}
        return pending

    def _ctx(self, run: Run, intent: Intent, now: datetime, entry_at: datetime, bars,
             exclude: tuple = ()) -> FunnelContext:
        account = self.broker.account()
        positions = self.broker.positions()
        day = entry_at.date().isoformat()
        open_risk = sum(risk.max_loss(t["qty"], t["entry_price"], t["stop"], t["side"]) or 0.0
                        for t in self.journal.open_trades() if t["stop"] is not None)
        short_exposure = sum(p["qty"] * (p.get("current_price") or p["avg_entry"])
                             for p in positions if p["side"] == "short")
        if entry_at > now:
            price = intent.ref_price            # decided after the close for the next window
        else:
            price = self.prices.get(intent.symbol) or self.broker.latest_price(intent.symbol) or intent.ref_price
        return FunnelContext(
            now_ny=now, entry_at=entry_at, cfg=self.cfg, run=run, universe=set(run.symbols or ()),
            account=account, positions=positions, pending_symbols=self._pending_symbols() - set(exclude),
            entries_today=self.journal.entries_today(day), run_entries_today=self.journal.entries_today(day, run.id),
            open_risk_usd=open_risk, short_exposure_usd=short_exposure, asset=self._asset(intent.symbol, now),
            bars=bars, price=price,
        )

    def _window(self, day: date) -> tuple[datetime, datetime]:
        g = self.cfg["global_risk"]
        after = g["no_entries_after"] or "15:59"
        start = at_hhmm(day, g["no_entries_before"] or "09:30")
        end = at_hhmm(day, after)
        if day in market_calendar.early_closes(day.year):
            end -= timedelta(hours=3)
        return start, end

    def _flat_by_time(self, day: date) -> datetime:
        t = at_hhmm(day, self.cfg["global_risk"]["flat_by"])
        return t - timedelta(hours=3) if day in market_calendar.early_closes(day.year) else t

    # ------------------------------------------------------------ jobs

    def _jobs(self, now: datetime, session: tuple[datetime, datetime]) -> None:
        open_, close = session
        if open_ <= now < close:
            self._send_queued(now)
            self._flat_by(now)
        if open_ <= now <= close:
            self._equity(now)
        if now >= close + SWING_PASS_DELAY and not self._swing_done(now.date()):
            self._swing_pass(now)

    def _swing_done(self, day: date) -> bool:
        last = self.journal.last_event("swing.pass")
        return bool(last and last["payload"].get("day") == day.isoformat())

    def _swing_pass(self, now: datetime) -> None:
        day = now.date().isoformat()
        counts = {"evaluated": 0, "entries": 0, "exits": 0}
        for run in runs_from_config(self.cfg):
            if run.style != "swing" or run.entry.get("kind") != "strategy":
                continue
            for symbol in run.symbols or ():
                try:
                    self._swing_symbol(run, symbol, now, day, counts)
                except Exception as exc:
                    logger.exception("swing pass %s %s", run.id, symbol)
                    self.bus.publish("error", {"message": f"swing pass {run.id} {symbol}: {exc}"},
                                     run_id=run.id, symbol=symbol)
        self.bus.publish("swing.pass", {"day": day, **counts})

    def _swing_symbol(self, run: Run, symbol: str, now: datetime, day: str, counts: dict) -> None:
        bars = self.broker.daily_bars(symbol, now, completed_today=True)
        prev = self.journal.latest_setup(run.id, symbol)
        acted = ((prev or {}).get("detail") or {}).get("acted_signal_bar")
        trade = next((t for t in self.journal.open_trades()
                      if t["symbol"] == symbol and t["run_id"] == run.id), None)
        ev = evaluate_daily(run, symbol, bars, position_side=trade["side"] if trade else None,
                            acted_signal_bar=acted)
        counts["evaluated"] += 1
        state, sentence = ev.state, ev.sentence
        detail = {**ev.detail, "acted_signal_bar": acted}
        for intent in ev.intents:
            if intent.kind == "exit":
                counts["exits"] += 1
                try:
                    self.executor.exit_position(symbol, "signal", now=now)
                except ExecutorError as exc:
                    self.bus.publish("error", {"message": f"{run.id} exit {symbol}: {exc}"},
                                     run_id=run.id, symbol=symbol)
                continue
            counts["entries"] += 1
            detail["acted_signal_bar"] = intent.signal_bar
            state, sentence, extra = self._decide_entry(run, intent, now, bars)
            detail.update(extra)
        self._set_setup(run.id, symbol, day, state, now, side=(ev.intents[-1].side if ev.intents else None),
                        level=ev.level, distance_pct=ev.distance_pct, sentence=sentence, detail=detail)

    def _decide_entry(self, run: Run, intent: Intent, now: datetime, bars) -> tuple[S, str, dict]:
        entry_day = next_session_day(now.date())
        entry_at, window_end = self._window(entry_day)
        if self.paused:
            return S.BLOCKED, "bot paused - no new entries", {}
        decision = run_funnel(intent, self._ctx(run, intent, now, entry_at, bars))
        if decision.outcome == "blocked":
            did = self.executor.record_decision(decision, intent, now=now, config_hash=self.cfg_hash)
            self.bus.publish("decision", {"id": did, "outcome": "blocked", "summary": decision.summary()},
                             run_id=run.id, symbol=intent.symbol)
            return S.BLOCKED, decision.summary(), {"decision_id": did}
        if decision.outcome == "proposed":
            row = self.executor.propose(intent, decision, now=now, config_hash=self.cfg_hash,
                                        expires_at=window_end)
            return S.PROPOSED, f"proposal #{row['id']}: {decision.summary()}", {"proposal_id": row["id"]}
        queued = {"intent": asdict(intent), "entry_at": _iso(entry_at)}
        return S.TRIGGERED, f"queued for {entry_at:%a %H:%M}: {decision.summary()}", {"queued": queued}

    def _send_queued(self, now: datetime) -> None:
        for row in self.journal.setups_in_state(S.TRIGGERED.value):
            queued = (row["detail"] or {}).get("queued")
            if not queued:
                continue
            entry_at = datetime.fromisoformat(queued["entry_at"])
            if now < entry_at:
                continue
            setup = Setup.from_row(row)
            detail = {k: v for k, v in setup.detail.items() if k != "queued"}
            intent = Intent(**queued["intent"])
            run = self._run(intent.run_id)
            _, window_end = self._window(entry_at.date())
            if now.date() > entry_at.date() or now > window_end:
                self._store_setup(transition(setup, S.EXPIRED, sentence="entry window passed before it was sent",
                                             detail=detail), now)
            elif run is None:
                self._store_setup(transition(setup, S.EXPIRED, sentence=f"run {intent.run_id} is disabled",
                                             detail=detail), now)
            elif self.paused:
                self._store_setup(transition(setup, S.BLOCKED, sentence="bot paused - not sent",
                                             detail=detail), now)
            else:
                bars = self.broker.daily_bars(intent.symbol, now)
                ctx = self._ctx(run, intent, now, now, bars, exclude=(intent.symbol,))
                res = self.executor.send(intent, ctx, now=now, config_hash=self.cfg_hash)
                detail["decision_id"] = res.decision_id
                state = S.ENTERED if res.ok else S.BLOCKED
                sentence = res.decision.summary() if res.decision else res.error
                self._store_setup(transition(setup, state, sentence=res.error or sentence, detail=detail), now)
                self._broker_dirty = True

    def _flat_by(self, now: datetime) -> None:
        if now < self._flat_by_time(now.date()):
            return
        for trade in self.journal.open_trades():
            if not trade["flat_by"]:
                continue
            already = [o for o in self.journal.orders_matching(symbol=trade["symbol"], since=now.date().isoformat())
                       if o["kind"] == "flat_by"]
            if already:
                continue
            try:
                self.executor.exit_position(trade["symbol"], "flat_by", now=now)
                self._broker_dirty = True
            except ExecutorError as exc:
                self.bus.publish("error", {"message": f"flat-by {trade['symbol']}: {exc}"}, symbol=trade["symbol"])

    def _expire_proposals(self, now: datetime) -> None:
        for e in self.executor.expire_due(now, self.prices, self.cfg["approval_max_drift_pct"]):
            row = self.journal.decision(e["id"])
            self._move_latest(row["run_id"], row["symbol"], S.EXPIRED, now, sentence=f"proposal {e['reason']}")

    def _equity(self, now: datetime) -> None:
        minute = now.replace(second=0, microsecond=0)
        key = _iso(minute)
        if key == self._equity_minute:
            return
        try:
            account = self.broker.account()
            positions = self.broker.positions()
        except Exception as exc:
            logger.warning("equity snapshot skipped: %s", exc)
            return
        open_pnl = round(sum(p.get("unrealized_pl") or 0.0 for p in positions), 2)
        self.journal.add_equity(key, account["equity"], account["cash"], open_pnl)
        self._equity_minute = key
        self.bus.publish("equity", {"ts": key, "equity": account["equity"], "cash": account["cash"],
                                    "open_pnl": open_pnl}, store=False)

    # ------------------------------------------------------------ inbox

    def _drain(self, now: datetime) -> None:
        while True:
            if self._early:
                item = self._early.pop(0)
            else:
                try:
                    item = self.inbox.get_nowait()
                except queue.Empty:
                    return
            try:
                self._handle(item, now)
            except Exception as exc:
                logger.exception("inbox item failed: %s", item.get("kind"))
                self.bus.publish("error", {"message": f"{item.get('kind')} failed: {exc}"})

    def _handle(self, item: dict, now: datetime) -> None:
        kind = item.get("kind")
        if kind == "trade_update":
            self._on_trade_update(item, now)
        elif kind == "quote":
            self._on_quote(item)
        elif kind == "stream_status":
            self._stream_state[item["stream"]] = item["status"]
            self.bus.publish("stream.status", item, store=False)
            if item["status"] == "down":
                self.bus.publish("error", {"message": f"{item['stream']} stream down: {item['detail']}"})
        elif kind == "stream_error":
            self.bus.publish("error", {"message": f"{item['stream']} stream: {item['error']}"})
        elif kind == "command":
            self._on_command(item, now)
        else:
            logger.warning("unknown inbox item %s", kind)

    # ------------------------------------------------------------ fills

    def _on_trade_update(self, item: dict, now: datetime) -> None:
        order, event = item["order"], item["event"]
        row = self.journal.order(order["id"])
        if row:
            self.journal.upsert_order(order["id"], status=order["status"], filled_qty=order["filled_qty"],
                                      filled_avg=order["filled_avg"])
        symbol = order["symbol"]
        trade = next((t for t in self.journal.open_trades() if t["symbol"] == symbol), None)
        is_entry = (row is not None and row["kind"] == "entry") or (
            row is None and (order.get("client_order_id") or "").startswith("pb-") and trade is None)
        if event in ("fill", "partial_fill"):
            self._broker_dirty = True
            exit_side = None if trade is None else ("sell" if trade["side"] == "long" else "buy")
            if is_entry:
                self._entry_fill(row, order, trade, now)
            elif trade is not None and order["side"] == exit_side:
                self._exit_fill(row, order, trade, item, now)
            else:
                self.bus.publish("fill", {"order_id": order["id"], "side": order["side"], "qty": item.get("qty"),
                                          "price": item.get("price"), "kind": "untracked"}, symbol=symbol)
        elif event in ("canceled", "expired", "rejected"):
            if is_entry and not order["filled_qty"]:
                if row and row["decision_id"]:
                    outcome = "rejected" if event == "rejected" else "expired"
                    self.journal.update_decision(row["decision_id"], outcome=outcome, failed_stage="execute")
                    d = self.journal.decision(row["decision_id"])
                    self._move_latest(d["run_id"], symbol, S.BLOCKED, now,
                                      sentence=f"entry order {event} at Alpaca")
            self.bus.publish(f"order.{'rejected' if event == 'rejected' else 'canceled'}",
                             {"order_id": order["id"], "status": order["status"], "event": event,
                              "kind": row["kind"] if row else None}, symbol=symbol)
        else:
            self.bus.publish("order.updated", {"order_id": order["id"], "status": order["status"], "event": event},
                             symbol=symbol)

    def _entry_fill(self, row: dict | None, order: dict, trade: dict | None, now: datetime) -> None:
        qty, price = order["filled_qty"], order["filled_avg"]
        if trade is not None:
            self.journal.update_trade(trade["id"], qty=qty, entry_price=price)
            self.bus.publish("fill", {"order_id": order["id"], "kind": "entry", "qty": qty, "price": price,
                                      "trade_id": trade["id"]}, run_id=trade["run_id"], symbol=order["symbol"])
            return
        decision = self.journal.decision(row["decision_id"]) if row and row["decision_id"] else None
        plan = (decision or {}).get("plan") or {}
        run_id = (decision or {}).get("run_id") or (order.get("client_order_id") or "pb-adopted").split("-")[1]
        run = self._run(run_id)
        side = "long" if order["side"] == "buy" else "short"
        tid = self.journal.open_trade(
            run_id=run_id, symbol=order["symbol"], side=side, qty=qty, entry_ts=_iso(now), entry_price=price,
            stop=plan.get("stop"), target=plan.get("target"), decision_id=(decision or {}).get("id"),
            score=(decision or {}).get("score"), llm_mode=(decision or {}).get("llm_mode"),
            llm_verdict=(decision or {}).get("llm_verdict"), config_hash=(decision or {}).get("config_hash"),
            flat_by=self.cfg["global_risk"]["flat_by"] if run and run.style == "day" else None,
        )
        if row:
            self.journal.upsert_order(order["id"], trade_id=tid)
        else:
            record_order(self.journal, order, "entry", trade_id=tid)
        self._move_latest(run_id, order["symbol"], S.MANAGING, now, sentence=f"holding {side} {qty:g} @ {price:.2f}")
        payload = {"trade_id": tid, "side": side, "qty": qty, "entry_price": price, "stop": plan.get("stop"),
                   "target": plan.get("target")}
        self.bus.publish("fill", {"order_id": order["id"], "kind": "entry", **payload},
                         run_id=run_id, symbol=order["symbol"])
        self.bus.publish("position.opened", payload, run_id=run_id, symbol=order["symbol"])

    def _exit_fill(self, row: dict | None, order: dict, trade: dict, item: dict, now: datetime) -> None:
        price = order["filled_avg"] or item.get("price")
        remaining = item.get("position_qty")
        remaining = abs(remaining) if remaining is not None else max(trade["qty"] - order["filled_qty"], 0.0)
        if remaining > 1e-9:
            self.journal.update_trade(trade["id"], qty=remaining)
            self.bus.publish("fill", {"order_id": order["id"], "kind": "partial exit", "qty": order["filled_qty"],
                                      "price": price, "trade_id": trade["id"], "remaining": remaining},
                             run_id=trade["run_id"], symbol=order["symbol"])
            return
        kind = row["kind"] if row else None
        if kind in (None, "entry", "protective"):
            kind = EXIT_REASON_BY_TYPE.get(order["type"], "broker")
        if row is None:
            record_order(self.journal, order, kind, trade_id=trade["id"])
        closed = self.journal.close_trade(trade["id"], exit_ts=_iso(now), exit_price=price, exit_reason=kind)
        self._extremes.pop(trade["id"], None)
        self._move_latest(trade["run_id"], order["symbol"], S.EXITED, now,
                          sentence=f"closed by {kind} @ {price:.2f}: {closed['pnl']:+.2f} ({closed['r'] or 0:+.2f}R)")
        self.bus.publish("fill", {"order_id": order["id"], "kind": kind, "qty": order["filled_qty"], "price": price,
                                  "trade_id": trade["id"]}, run_id=trade["run_id"], symbol=order["symbol"])
        self.bus.publish("trade.closed", {"trade_id": trade["id"], "side": trade["side"], "exit_reason": kind,
                                          "entry_price": trade["entry_price"], "exit_price": price,
                                          "qty": trade["qty"], "pnl": closed["pnl"], "r": closed["r"],
                                          "mae_r": closed["mae_r"], "mfe_r": closed["mfe_r"]},
                         run_id=trade["run_id"], symbol=order["symbol"])

    def _on_quote(self, item: dict) -> None:
        symbol, price = item["symbol"], item["price"]
        self.prices[symbol] = price
        self.bus.publish("quote", {"price": price, "ts": item.get("timestamp")}, symbol=symbol, store=False)
        for t in self.journal.open_trades():
            if t["symbol"] != symbol or t["stop"] is None:
                continue
            at_risk = risk.risk_per_share(t["entry_price"], t["stop"], t["side"])
            if at_risk <= 0:
                continue
            r_now = ((price - t["entry_price"]) if t["side"] == "long" else (t["entry_price"] - price)) / at_risk
            lo, hi = self._extremes.get(t["id"], [t["mae_r"] or 0.0, t["mfe_r"] or 0.0])
            lo2, hi2 = round(min(lo, r_now), 2), round(max(hi, r_now), 2)
            if (lo2, hi2) != (lo, hi):
                self.journal.update_trade(t["id"], mae_r=lo2, mfe_r=hi2)
            self._extremes[t["id"]] = [lo2, hi2]

    # ------------------------------------------------------------ commands

    def _on_command(self, item: dict, now: datetime) -> None:
        cid, cmd, args = item["id"], item["cmd"], item.get("args") or {}
        try:
            result = self._command(cmd, args, now)
            status = "done"
        except (ExecutorError, pcfg.ConfigError, ValueError, KeyError) as exc:
            result, status = {"error": str(exc)}, "error"
        except Exception as exc:
            logger.exception("command %s failed", cmd)
            result, status = {"error": str(exc)}, "error"
        self.journal.finish_command(cid, status, result)
        self.bus.publish("command", {"id": cid, "kind": cmd, "args": args, "status": status, "result": result})
        self._broker_dirty = True

    def _command(self, cmd: str, args: dict, now: datetime) -> dict:
        if cmd == "pause":
            self.paused = True
            self.bus.publish("bot.paused", {})
            return {"paused": True}
        if cmd == "resume":
            self.paused = False
            self.bus.publish("bot.resumed", {})
            return {"paused": False}
        if cmd == "flatten_all":
            return {"orders": [o["id"] for o in self.executor.flatten_all(now=now)]}
        if cmd == "close_position":
            return {"order": self.executor.close_position(args["symbol"], now=now)["id"]}
        if cmd == "set_approval":
            pcfg.set_approval(self.config_path, args["mode"])
            self._reload_config(force=True)
            return {"approval_mode": self.cfg["approval_mode"]}
        if cmd == "approve":
            return self._approve(int(args["proposal_id"]), now)
        if cmd == "reject":
            pid = int(args["proposal_id"])
            row = self.journal.decision(pid)
            self.executor.reject(pid, now=now)
            self._move_latest(row["run_id"], row["symbol"], S.BLOCKED, now, sentence="rejected in the Paper desk")
            return {"proposal_id": pid, "outcome": "rejected"}
        raise ValueError(f"unknown command {cmd!r}")

    def _approve(self, pid: int, now: datetime) -> dict:
        row = self.journal.decision(pid)
        if row is None or row["outcome"] != "proposed":
            raise ExecutorError(f"proposal {pid} is not pending")
        start, end = self._window(now.date())
        if self.market != "open" or not (start <= now <= end):
            raise ExecutorError(f"approve during the entry window ({start:%H:%M}-{end:%H:%M} NY, market open)")
        if self.paused:
            raise ExecutorError("the bot is paused - resume before approving")
        intent = Intent(**row["plan"]["intent"])
        run = self._run(intent.run_id)
        if run is None:
            raise ExecutorError(f"run {intent.run_id} is disabled")
        bars = self.broker.daily_bars(intent.symbol, now)
        res = self.executor.approve(pid, self._ctx(run, intent, now, now, bars), now=now, config_hash=self.cfg_hash)
        self._move_latest(run.id, intent.symbol, S.ENTERED if res.ok else S.BLOCKED, now,
                          sentence=res.error or res.decision.summary())
        if not res.ok:
            raise ExecutorError(res.error)
        return {"proposal_id": pid, "order": res.order["id"]}

    # ------------------------------------------------------------ broker cache, quotes, heartbeat

    def _refresh_broker(self, now: datetime) -> None:
        stale = self._broker_at is None or (now - self._broker_at).total_seconds() >= BROKER_REFRESH_S
        if not (stale or self._broker_dirty):
            return
        try:
            self._account = self.broker.account()
            self._positions = self.broker.positions()
            self._broker_at, self._broker_dirty = now, False
        except Exception as exc:
            logger.warning("broker refresh failed: %s", exc)

    def _update_quote_symbols(self) -> None:
        if self.quotes is None:
            return
        symbols = {t["symbol"] for t in self.journal.open_trades()} | {p["symbol"] for p in self._positions or []}
        self.quotes.set_symbols(symbols)

    def _next_wake(self, now: datetime) -> str | None:
        candidates = []
        for row in self.journal.setups_in_state(S.TRIGGERED.value):
            q = (row["detail"] or {}).get("queued")
            if q:
                candidates.append(datetime.fromisoformat(q["entry_at"]))
        session = market_calendar.session(now.date())
        if session and not self._swing_done(now.date()):
            candidates.append(session[1] + SWING_PASS_DELAY)
        if any(t["flat_by"] for t in self.journal.open_trades()):
            candidates.append(self._flat_by_time(now.date()))
        future = [c for c in candidates if c > now]
        return _iso(min(future)) if future else None

    def _streams_health(self) -> dict:
        out = {name: s.health() for name, s in self.streams.items()}
        if self.quotes is not None:
            out["quotes"] = self.quotes.health()
        return out

    def _heartbeat(self, now: datetime) -> None:
        self.journal.write_heartbeat(ts=_iso(now), pid=self.pid, paused=self.paused, market=self.market,
                                     next_wake=self.next_wake, streams=self._streams_health(),
                                     config_hash=self.cfg_hash)

    def _build_snapshot(self, now: datetime) -> dict:
        day = now.date().isoformat()
        trades = {t["symbol"]: t for t in self.journal.open_trades()}
        positions = []
        for p in self._positions or []:
            t = trades.get(p["symbol"])
            price = self.prices.get(p["symbol"]) or p.get("current_price") or p["avg_entry"]
            sign = 1 if p["side"] == "long" else -1
            pnl = round(sign * (price - p["avg_entry"]) * p["qty"], 2)
            r_now = None
            if t and t["stop"] is not None:
                at_risk = risk.risk_per_share(t["entry_price"], t["stop"], t["side"])
                r_now = round(sign * (price - t["entry_price"]) / at_risk, 2) if at_risk > 0 else None
            positions.append({**p, "price": price, "pnl": pnl, "r": r_now,
                              "trade": {k: t[k] for k in ("id", "run_id", "stop", "target", "entry_ts", "adopted",
                                                          "flat_by", "mae_r", "mfe_r")} if t else None})
        g = self.cfg["global_risk"]
        entries = self.journal.entries_today(day)
        open_risk = sum(risk.max_loss(t["qty"], t["entry_price"], t["stop"], t["side"]) or 0.0
                        for t in trades.values() if t["stop"] is not None)
        proposals = [{"id": p["id"], "run_id": p["run_id"], "symbol": p["symbol"], "side": p["side"], "qty": p["qty"],
                      "max_loss": p["max_loss"], "plan": p["plan"], "stages": p["stages"]}
                     for p in self.executor.pending_proposals()]
        setups = self.journal.setups(day) + [s for s in self.journal.setups_in_state(S.TRIGGERED.value)
                                             if s["day"] != day]
        return {
            "ts": _iso(now), "pid": self.pid, "paused": self.paused, "market": self.market,
            "next_wake": self.next_wake, "streams": self._streams_health(),
            "config": {"config_hash": self.cfg_hash, "approval_mode": self.cfg["approval_mode"],
                       "llm_mode": self.cfg["llm_mode"],
                       "runs": [{"id": r.id, "style": r.style, "enabled": r.enabled}
                                for r in runs_from_config(self.cfg, enabled_only=False)]},
            "account": self._account, "positions": positions, "proposals": proposals, "setups": setups,
            "counters": {"entries_today": len(entries), "max_trades_per_day": g["max_trades_per_day"],
                         "daily_loss_used": round(sum(e["max_loss"] or 0.0 for e in entries), 2),
                         "max_daily_loss_usd": g["max_daily_loss_usd"], "open_risk_usd": round(open_risk, 2),
                         "max_open_risk_usd": g["max_open_risk_usd"]},
            "last_event_id": self.journal.last_event_id(),
        }
