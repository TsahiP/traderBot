"""Authorized Telegram command listener for the live paper-trading bot."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import requests

import alpaca_service
import config
import notifier

logger = logging.getLogger("tradebot")

HELP_TEXT = (
    "TradeBot commands:\n"
    "/status - bot, account, and position status\n"
    "/pnl - today's account P&L\n"
    "/pause - block new entries (exits stay enabled)\n"
    "/resume - allow new entries\n"
    "/backtest <SYMBOL> <STRATEGY> - run a backtest\n"
    "Strategies: sma_crossover, vwap_reversion, "
    "opening_range_breakout, rsi_mean_reversion"
)


@dataclass
class RuntimeControl:
    """Thread-safe state shared by trading and Telegram threads."""

    started_at: float = field(default_factory=time.time)
    _paused: bool = False
    _listener_healthy: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    backtest_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def set_paused(self, value: bool) -> None:
        with self._lock:
            self._paused = value

    def set_listener_healthy(self, value: bool) -> None:
        with self._lock:
            self._listener_healthy = value

    def listener_healthy(self) -> bool:
        with self._lock:
            return self._listener_healthy


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _signed_money(value: float) -> str:
    return f"{'+' if value >= 0 else '-'}${abs(value):,.2f}"


def _format_uptime(started_at: float) -> str:
    seconds = max(0, int(time.time() - started_at))
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"


class TelegramCommandBot:
    """Long-poll Telegram and dispatch authorized commands."""

    def __init__(
        self,
        trading_client,
        control: RuntimeControl,
        backtest_runner: Callable | None = None,
    ) -> None:
        self.trading = trading_client
        self.control = control
        self.backtest_runner = backtest_runner
        self.offset: int | None = None
        self.stop_event = threading.Event()

    @property
    def updates_url(self) -> str:
        return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"

    def run(self) -> None:
        logger.info("Telegram command listener started")
        while not self.stop_event.is_set():
            try:
                response = requests.get(
                    self.updates_url,
                    params={"timeout": 25, "offset": self.offset},
                    timeout=35,
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok"):
                    raise ValueError("Telegram returned an unsuccessful response")
                self.control.set_listener_healthy(True)
                for update in payload.get("result", []):
                    self.offset = max(self.offset or 0, int(update["update_id"]) + 1)
                    self.handle_update(update)
            except Exception as exc:
                self.control.set_listener_healthy(False)
                logger.warning("Telegram polling failed: %s", exc)
                self.stop_event.wait(3)

    def handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            if chat_id:
                logger.warning("Ignored unauthorized Telegram chat id %s", chat_id)
            return

        text = str(message.get("text") or "").strip()
        if not text.startswith("/"):
            return
        command, *args = text.split()
        command = command.split("@", 1)[0].lower()
        try:
            self.dispatch(chat_id, command, args)
        except Exception as exc:
            logger.exception("Telegram command %s failed: %s", command, exc)
            notifier.send_telegram_to(chat_id, "Command failed. Check bot logs and try again.")

    def dispatch(self, chat_id: str, command: str, args: list[str]) -> None:
        if command in ("/start", "/help"):
            notifier.send_telegram_to(chat_id, HELP_TEXT)
        elif command == "/pause":
            self.control.set_paused(True)
            notifier.send_telegram_to(
                chat_id, "New entries paused. Position exits remain enabled."
            )
        elif command == "/resume":
            self.control.set_paused(False)
            notifier.send_telegram_to(chat_id, "New entries resumed.")
        elif command == "/status":
            notifier.send_telegram_to(chat_id, self._status_text())
        elif command == "/pnl":
            notifier.send_telegram_to(chat_id, self._pnl_text())
        elif command == "/backtest":
            self._start_backtest(chat_id, args)
        else:
            notifier.send_telegram_to(chat_id, f"Unknown command.\n\n{HELP_TEXT}")

    def _status_text(self) -> str:
        snapshot = alpaca_service.get_account_snapshot(self.trading)
        account = snapshot["account"]
        clock = self.trading.get_clock()
        positions = snapshot["positions"]
        lines = [
            "TradeBot status",
            f"Trading: {'PAUSED (entries)' if self.control.is_paused() else 'ACTIVE'}",
            f"Market: {'OPEN' if clock.is_open else 'CLOSED'}",
            f"Listener: {'healthy' if self.control.listener_healthy() else 'degraded'}",
            f"Uptime: {_format_uptime(self.control.started_at)}",
            f"Equity: {_money(account['equity'])}",
            f"Buying power: {_money(account['buying_power'])}",
            f"Positions: {len(positions)}",
        ]
        for position in positions[:12]:
            lines.append(
                f"- {position['symbol']} x{position['qty']:g} "
                f"@ {_money(position['current'])} "
                f"({_signed_money(position['unrealized_pl'])})"
            )
        if len(positions) > 12:
            lines.append(f"... and {len(positions) - 12} more")
        return "\n".join(lines)

    def _pnl_text(self) -> str:
        snapshot = alpaca_service.get_account_snapshot(self.trading)
        account = snapshot["account"]
        unrealized = sum(p["unrealized_pl"] for p in snapshot["positions"])
        return "\n".join(
            [
                "Today's P&L",
                f"Account day P&L: {_signed_money(account['day_pnl'])}",
                f"Open-position unrealized: {_signed_money(unrealized)}",
                f"Current equity: {_money(account['equity'])}",
            ]
        )

    def _start_backtest(self, chat_id: str, args: list[str]) -> None:
        if len(args) != 2:
            notifier.send_telegram_to(
                chat_id, "Usage: /backtest <SYMBOL> <STRATEGY>\n\n" + HELP_TEXT
            )
            return
        if not self.control.backtest_lock.acquire(blocking=False):
            notifier.send_telegram_to(chat_id, "A backtest is already running. Try again later.")
            return

        symbol, strategy_id = args[0].upper(), args[1].lower()
        notifier.send_telegram_to(chat_id, f"Running {strategy_id} backtest for {symbol}...")
        thread = threading.Thread(
            target=self._run_backtest,
            args=(chat_id, symbol, strategy_id),
            name="telegram-backtest",
            daemon=True,
        )
        thread.start()

    def _run_backtest(self, chat_id: str, symbol: str, strategy_id: str) -> None:
        try:
            from strategies import STRATEGIES

            if strategy_id not in STRATEGIES:
                notifier.send_telegram_to(chat_id, f"Unknown strategy '{strategy_id}'.\n\n{HELP_TEXT}")
                return
            runner = self.backtest_runner
            if runner is None:
                from dashboard import run_backtest_from

                runner = run_backtest_from
            spec = STRATEGIES[strategy_id]
            payload, status, error = runner(
                {
                    "symbol": symbol,
                    "strategy": strategy_id,
                    "timeframe": spec["default_timeframe"],
                }
            )
            if status != 200 or payload is None:
                notifier.send_telegram_to(chat_id, f"Backtest failed: {error or 'unknown error'}")
                return
            metrics = payload["metrics"]
            meta = payload["meta"]
            notifier.send_telegram_to(
                chat_id,
                "\n".join(
                    [
                        f"{meta['symbol']} - {meta['strategy_label']} ({meta['timeframe']})",
                        f"Period: {metrics['start']} to {metrics['end']}",
                        f"Return: {metrics['total_return']:.2%}",
                        f"CAGR: {metrics['cagr']:.2%}",
                        f"Max drawdown: {metrics['max_drawdown']:.2%}",
                        f"Trades: {metrics['trades']} | Win rate: {metrics['win_rate']:.1%}",
                        f"Costs: {_money(metrics['costs_total'])}",
                        f"Final equity: {_money(metrics['final_equity'])}",
                    ]
                ),
            )
        except Exception as exc:
            logger.exception("Telegram backtest failed: %s", exc)
            notifier.send_telegram_to(chat_id, "Backtest failed. Check bot logs and try again.")
        finally:
            self.control.backtest_lock.release()


def start_telegram_listener(
    trading_client, control: RuntimeControl
) -> threading.Thread | None:
    """Start the command listener when Telegram is configured."""
    if not config.TELEGRAM_ENABLED:
        return None
    bot = TelegramCommandBot(trading_client, control)
    thread = threading.Thread(
        target=bot.run,
        name="telegram-listener",
        daemon=True,
    )
    thread.start()
    return thread
