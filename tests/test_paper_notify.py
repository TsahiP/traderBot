"""paper.notify: Discord text per event, the config filter, retries in the
background, a missing webhook, and no error spam."""
import time

import discord_channel_routes as dcr
from paper import config as pcfg
from paper.notify import DiscordNotifier, format_event, webhook_url


def _event(type, payload, symbol="SPY", run_id="swing_sma"):
    return {"id": 1, "ts": "2026-10-30T09:45:00-04:00", "type": type, "run_id": run_id,
            "symbol": symbol, "payload": payload}


def _wait(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- formatting

def test_entry_fill_text():
    text = format_event(_event("fill", {"kind": "entry", "side": "long", "qty": 50, "entry_price": 120.1,
                                        "stop": 116.1, "target": 128.1, "trade_id": 3}))
    assert text.startswith("🟢") and "long 50 SPY @ 120.10" in text
    assert "stop 116.10" in text and "target 128.10" in text and "swing_sma" in text


def test_exit_fills_are_left_to_trade_closed():
    assert format_event(_event("fill", {"kind": "stop", "qty": 50, "price": 116.0})) is None


def test_trade_closed_win_and_loss():
    loss = format_event(_event("trade.closed", {"side": "long", "qty": 50, "exit_reason": "stop",
                                                "exit_price": 116.0, "pnl": -205.0, "r": -1.025}))
    assert loss.startswith("🔴") and "by stop @ 116.00" in loss and "-205.00" in loss and "-1.02R" in loss
    win = format_event(_event("trade.closed", {"side": "short", "qty": 5, "exit_reason": "target",
                                               "exit_price": 175.3, "pnl": 35.0, "r": 2.0}, symbol="NVDA"))
    assert win.startswith("🟢") and "short 5 NVDA" in win and "+35.00" in win and "+2.00R" in win
    no_r = format_event(_event("trade.closed", {"side": "long", "qty": 2, "exit_reason": "broker",
                                                "exit_price": 400.0, "pnl": 20.0, "r": None}))
    assert "R)" not in no_r


def test_proposal_error_and_lifecycle_texts():
    p = format_event(_event("proposal.created", {"id": 12, "ticket": "proposed: long 50 SPY @ ~100.00",
                                                 "expires_at": "2026-10-30T15:30:00-04:00"}))
    assert "#12" in p and "Paper desk" in p and "15:30" in p
    assert format_event(_event("error", {"message": "trading stream down"})).startswith("⚠️")
    assert "started" in format_event(_event("bot.start", {"pid": 9}, symbol=None, run_id=None))
    assert "stopped" in format_event(_event("bot.stop", {"pid": 9}, symbol=None, run_id=None))
    assert format_event(_event("setup.state", {})) is None


# ---------------------------------------------------------------- the notifier

def _notifier(sent, cfg=None, url="https://discord.test/hook", fail_times=0, raise_times=0):
    cfg = cfg or pcfg.default_config()
    state = {"fails": fail_times, "raises": raise_times}

    def send(u, text, suppress_embeds=False):
        if state["raises"]:
            state["raises"] -= 1
            raise ConnectionError("boom")
        if state["fails"]:
            state["fails"] -= 1
            return False
        sent.append((u, text))
        return True

    n = DiscordNotifier(lambda: cfg, resolve=lambda route: url, send=send, backoff=(0.0, 0.0, 0.0),
                        sleep=lambda s: None)
    n.start()
    return n


def test_filtered_events_are_sent_in_the_background():
    sent = []
    n = _notifier(sent)
    n(_event("trade.closed", {"side": "long", "qty": 1, "exit_reason": "target", "exit_price": 1.0,
                              "pnl": 1.0, "r": 1.0}))
    n(_event("setup.state", {}))
    assert _wait(lambda: len(sent) == 1)
    assert sent[0][0] == "https://discord.test/hook"
    n.stop()


def test_config_decides_which_events_go_out():
    sent = []
    cfg = pcfg.default_config()
    cfg["discord"]["events"] = ["error"]
    n = _notifier(sent, cfg=cfg)
    n(_event("trade.closed", {"side": "long", "qty": 1, "exit_reason": "target", "exit_price": 1.0,
                              "pnl": 1.0, "r": 1.0}))
    n(_event("error", {"message": "x"}))
    assert _wait(lambda: len(sent) == 1) and sent[0][1].startswith("⚠️")
    n.stop()


def test_failures_are_retried_and_exceptions_never_escape():
    sent = []
    n = _notifier(sent, fail_times=1, raise_times=1)
    n(_event("error", {"message": "retry me"}))
    assert _wait(lambda: len(sent) == 1)
    n.stop()


def test_no_webhook_means_nothing_is_sent():
    sent = []
    n = _notifier(sent, url=None)
    n(_event("error", {"message": "x"}))
    time.sleep(0.1)
    assert sent == [] and n.configured is False
    n.stop()


def test_the_same_error_is_not_repeated_within_the_window():
    sent = []
    n = _notifier(sent)
    for _ in range(3):
        n(_event("error", {"message": "quotes stream down: dns"}))
    n(_event("error", {"message": "something else"}))
    assert _wait(lambda: len(sent) == 2)
    time.sleep(0.05)
    assert len(sent) == 2
    n.stop()


def test_webhook_url_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_WEBHOOK_PAPER", "https://env/hook")
    assert webhook_url("paper") == "https://env/hook"
    monkeypatch.delenv("DISCORD_WEBHOOK_PAPER")
    import signals

    monkeypatch.setattr(signals, "_webhook_url_from_sync_file", lambda route: f"https://synced/{route}")
    assert webhook_url("paper") == "https://synced/paper"


def test_paper_desk_channel_syncs_to_the_paper_route():
    assert dcr.parse_channel_route("paper-desk") == "paper"
    assert dcr.parse_channel_route("paper_desk") == "paper"
    assert dcr.parse_channel_route("paper") == "paper"
    assert dcr.parse_channel_route("papers") is None
