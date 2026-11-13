import asyncio

import analystbot as ab
import discord
import pytest


@pytest.fixture(autouse=True)
def _no_extra_symbols(monkeypatch):
    monkeypatch.setattr(ab.config, "ANALYST_EXTRA_SYMBOLS", [])  # keep .env out of the tests


def test_menu_symbols_merges_extras_dedupes_and_drops_invalid(monkeypatch):
    monkeypatch.setattr(ab.config, "ANALYST_EXTRA_SYMBOLS", ["META", "nvda", "BTC-USD", "^VIX", "../x", "SPY"])
    cfg = {"lists": [{"symbols": ["NVDA", "AAPL"]}]}
    assert ab.menu_symbols(cfg) == ["AAPL", "META", "NVDA", "SPY"]


def test_menu_symbols_stocks_only_and_sorted():
    cfg = {
        "lists": [
            {"name": "Core", "symbols": ["NVDA", "BTC-USD", "nvda", "AAPL"]},
            {"name": "Other", "symbols": ["ETH-USD", "SPY"]},
        ]
    }
    assert ab.menu_symbols(cfg) == ["AAPL", "NVDA", "SPY"]


def test_menu_symbols_empty():
    assert ab.menu_symbols({"lists": []}) == []
    assert ab.menu_symbols({}) == []


def test_view_refresh_marks_changes():
    view = ab.AnalystView()
    assert view.refresh_symbols(["NVDA", "AAPL"]) is True
    assert view.symbols == ["NVDA", "AAPL"]
    assert len(view.children) == 1
    assert view.refresh_symbols(["NVDA", "AAPL"]) is False  # no change no rebuild
    assert view.refresh_symbols([]) is True
    assert len(view.children) == 0


def test_select_persists_and_defers():
    view = ab.AnalystView()
    view.refresh_symbols(["NVDA", "AAPL"])
    sel = view.children[0]
    assert isinstance(sel, ab.AnalystSelect)
    assert sel.custom_id == "fundi_analyst_select_0"
    assert [o.value for o in sel.options] == ["NVDA", "AAPL"]


def test_select_25_per_row():
    symbols = [f"S{i:03d}" for i in range(30)]
    view = ab.AnalystView()
    view.refresh_symbols(symbols)
    assert len(view.children) == 2
    assert [len(row.options) for row in view.children] == [25, 5]


def test_bot_token_missing_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(ab.config, "BASE_DIR", tmp_path)  # no .env file here
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        ab.bot_token()


def test_bot_token_reads_env(monkeypatch, tmp_path):
    monkeypatch.setattr(ab.config, "BASE_DIR", tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "abc")
    assert ab.bot_token() == "abc"


def test_select_callback_opens_modal_then_cooldown(monkeypatch):
    """Flow: first click opens the position modal, second (too fast) gets an ephemeral note."""
    opened: list[str] = []

    class _Resp:
        def __init__(self):
            self.ephemeral = None
            self.sent = []
            self.modal = None
            self.deferred = None

        async def send_message(self, content=None, ephemeral=None):
            self.ephemeral = ephemeral
            self.sent.append(content)

        async def send_modal(self, modal):
            self.modal = modal
            opened.append(getattr(modal, "symbol", "?"))

        async def defer(self, thinking=True):
            self.deferred = True

    class _Follow:
        async def send(self, content=None, **kw):
            pass

    class _Client:
        cooldown = {}
        inflight = set()

    class _Inter:
        def __init__(self):
            self.response = _Resp()
            self.followup = _Follow()

        user = type("U", (), {"id": 7})()
        client = _Client()
        values = ["NVDA"]

    view = ab.AnalystView()
    view.refresh_symbols(["NVDA"])
    sel = view.children[0]
    assert isinstance(sel, ab.AnalystSelect)
    assert sel.custom_id == "fundi_analyst_select_0"
    sel._values = ["NVDA"]  # what discord.py stores pre-callback (values is a property)
    _Client.cooldown.clear()

    inter1 = _Inter()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(sel.callback(inter1))
    assert opened == ["NVDA"]  # modal opened, no defer before it
    assert inter1.response.deferred is None

    inter2 = _Inter()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(sel.callback(inter2))
    assert inter2.response.sent and _Client.cooldown  # second click -> ephemeral cooldown note


def test_select_callback_inflight(monkeypatch):
    class _Resp:
        def __init__(self):
            self.sent = []
            self.modal = None

        async def send_message(self, content=None, ephemeral=None):
            self.sent.append(content)

        async def send_modal(self, modal):
            self.modal = modal

    class _Follow:
        async def send(self, content=None, **kw):
            pass

    class _Client:
        cooldown = {}
        inflight = {"NVDA"}

    class _Inter:
        def __init__(self):
            self.response = _Resp()
            self.followup = _Follow()

        user = type("U", (), {"id": 7})()
        client = _Client()
        values = ["NVDA"]

    view = ab.AnalystView()
    view.refresh_symbols(["NVDA"])
    sel = view.children[0]
    sel._values = ["NVDA"]
    _Client.cooldown.clear()

    inter = _Inter()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(sel.callback(inter))
    assert "already being analyzed" in inter.response.sent[0]
    assert inter.response.modal is None  # no modal on top of a running job


def test_parse_holding_valid_and_invalid():
    holding, err = ab.parse_holding("50", "105.2", "300")
    assert err is None and holding == {"qty": 50.0, "avg_price": 105.2, "risk_usd": 300.0}
    # both blank -> all cash
    holding, err = ab.parse_holding("", "", "")
    assert err is None and holding is None
    # qty without avg -> error; neither runs
    holding, err = ab.parse_holding("50", "", "300")
    assert holding is None and "together" in err
    # risk-only -> custom risk, still cash entry
    holding, err = ab.parse_holding(None, None, "150")
    assert err is None and holding == {"qty": None, "avg_price": None, "risk_usd": 150.0}
    # junk numbers
    holding, err = ab.parse_holding("$1,000", "2.5", None)
    assert err is None and holding["qty"] == 1000.0  # commas tolerated
    holding, err = ab.parse_holding("-5", "10", None)
    assert holding is None and "Shares" in err
    holding, err = ab.parse_holding(None, None, "abc")
    assert holding is None and "risk" in err


def _set_input(item, text):
    item._value = text  # TextInput.value is a read-only property in this fork


def test_holding_modal_submit_paths(monkeypatch):
    runs: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(ab, "_run_and_post", _Recorder(runs))
    modal = ab.HoldingModal("NVDA")
    assert modal.shares.label and modal.avg.label and modal.risk.label

    class _Resp:
        def __init__(self):
            self.ephemeral = None
            self.sent = []

        async def send_message(self, content=None, ephemeral=None):
            self.ephemeral = ephemeral
            self.sent.append(content)

    class _Inter:
        def __init__(self):
            self.response = _Resp()

        user = type("U", (), {"id": 7})()

    # valid submit -> shared flow with parsed holding
    _set_input(modal.shares, "50")
    _set_input(modal.avg, "105.2")
    _set_input(modal.risk, "300")
    inter = _Inter()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(modal.on_submit(inter))
    assert runs and runs[-1] == ("NVDA", {"qty": 50.0, "avg_price": 105.2, "risk_usd": 300.0})

    # invalid submit -> ephemeral error, no run
    _set_input(modal.shares, "50")
    _set_input(modal.avg, "")
    _set_input(modal.risk, "300")
    inter = _Inter()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(modal.on_submit(inter))
    assert "together" in inter.response.sent[0] and inter.response.ephemeral
    assert len(runs) == 1


class _Recorder:
    def __init__(self, runs):
        self.runs = runs

    async def __call__(self, interaction, symbol, holding):
        self.runs.append((symbol, holding))


def test_run_and_post_chunks_files_and_errors(monkeypatch):
    monkeypatch.setattr(ab.config, "ANALYST_REPLY_MODE", "channel")
    calls = {"gather": [], "run": []}

    def fake_gather(symbol, holding=None):
        calls["gather"].append((symbol, holding))
        return {
            "symbol": symbol, "updated": "u", "fields": {}, "quarters": [],
            "news": [], "recent_signals": [], "watchlists": [],
            "snapshot_errors": [], "fetch_errors": [], "charts": {"1d": b"png1d", "15m": b"png15m"},
        }

    def fake_run(data):
        calls["run"].append(data)
        return "## Verdict\nGO - confidence 4/5", "fake-model"

    monkeypatch.setattr(ab, "gather_symbol_data", fake_gather)
    monkeypatch.setattr(ab, "run_analysis", fake_run)
    monkeypatch.setattr(ab, "format_reply", lambda s, a, d: ["**part 1**", "**part 2**"])

    class _Resp:
        def __init__(self):
            self.deferred = False

        async def defer(self, thinking=True, ephemeral=False):
            self.deferred = True

    sent: list[dict] = []

    class _Follow:
        async def send(self, content=None, files=None, **kw):
            sent.append({"content": content, "files": files or []})

    reposts: list = []

    class _Client:
        cooldown = {}
        inflight = set()

        async def repost_menu(self, channel):
            reposts.append(channel)

    class _Inter:
        def __init__(self):
            self.response = _Resp()
            self.followup = _Follow()

        user = type("U", (), {"id": 7})()
        client = _Client()
        values = ["NVDA"]
        channel = "chan"

    inter = _Inter()
    inter.client = _Client()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        ab._run_and_post(inter, "NVDA", {"qty": 10.0, "avg_price": 100.0, "risk_usd": 250.0})
    )
    assert calls["gather"] == [("NVDA", {"qty": 10.0, "avg_price": 100.0, "risk_usd": 250.0})]
    assert inter.response.deferred is True
    assert [m["content"] for m in sent] == ["**part 1**", "**part 2**"]
    assert [f.filename for f in sent[0]["files"]] == ["NVDA_1d.png", "NVDA_15m.png"]
    assert not _Client.inflight  # cleaned up
    # error path: gather blows up -> user sees a failure followup
    def boom(symbol, holding=None):
        raise RuntimeError("no data")

    monkeypatch.setattr(ab, "gather_symbol_data", boom)
    inter = _Inter()
    inter.client = _Client()
    _Client.inflight.clear()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(ab._run_and_post(inter, "NVDA", None))
    assert "failed" in sent[-1]["content"]
    assert not _Client.inflight
    assert reposts == ["chan", "chan"]  # menu re-posted after the success AND the failure


def test_parse_holding_invest_amount():
    holding, err = ab.parse_holding("", "", "", "2000")
    assert err is None and holding == {"qty": None, "avg_price": None, "risk_usd": None, "invest_usd": 2000.0}
    holding, err = ab.parse_holding("50", "105", "300", "$1,500")
    assert err is None and holding["invest_usd"] == 1500.0 and holding["qty"] == 50.0
    holding, err = ab.parse_holding("", "", "", "abc")
    assert holding is None and "Investment" in err
    holding, err = ab.parse_holding("", "", "", "-5")
    assert holding is None and "Investment" in err
    assert ab.parse_holding("50", "105.2", "300")[0] == {"qty": 50.0, "avg_price": 105.2, "risk_usd": 300.0}


def test_modal_has_invest_field_and_passes_it(monkeypatch):
    runs: list = []
    monkeypatch.setattr(ab, "_run_and_post", _Recorder(runs))
    modal = ab.HoldingModal("NVDA")
    assert modal.invest.label and "1:" in modal.invest.label
    _set_input(modal.shares, "")
    _set_input(modal.avg, "")
    _set_input(modal.risk, "")
    _set_input(modal.invest, "2500")
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(modal.on_submit(object()))
    assert runs[-1][1]["invest_usd"] == 2500.0


def test_repost_menu_sends_new_then_deletes_old():
    log: list = []

    class _Msg:
        def __init__(self, name):
            self.name = name

        async def delete(self):
            log.append(("delete", self.name))

    class _Chan:
        async def send(self, content, view=None):
            log.append(("send", view is not None))
            return _Msg("new")

    bot = ab.AnalystBot()
    bot.view.refresh_symbols(["NVDA"])
    bot.menu_message = _Msg("old")
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(bot.repost_menu(_Chan()))
    assert log == [("send", True), ("delete", "old")]
    assert bot.menu_message.name == "new"
    # no tickers / no channel -> nothing happens
    bot2 = ab.AnalystBot()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(bot2.repost_menu(_Chan()))
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(bot.repost_menu(None))
    assert log == [("send", True), ("delete", "old")]


class _DmHarness:
    """Fakes for the DM-delivery tests: records DMs, channel posts, followups."""

    def __init__(self, dm_error=None, channel=True):
        self.dms, self.channel_posts, self.followups, self.reposts = [], [], [], []
        self.deferred_ephemeral = None
        outer = self

        class _User:
            id = 7

            async def send(self, content=None, files=None, **kw):
                if dm_error:
                    raise dm_error
                outer.dms.append((content, len(files or [])))

        class _Chan:
            async def send(self, content=None, files=None, **kw):
                outer.channel_posts.append((content, len(files or [])))

        class _Resp:
            async def defer(self, thinking=True, ephemeral=False):
                outer.deferred_ephemeral = ephemeral

        class _Follow:
            async def send(self, content=None, files=None, ephemeral=False, **kw):
                outer.followups.append((content, ephemeral))

        class _Client:
            inflight = set()

            async def repost_menu(self, channel):
                outer.reposts.append(channel)

        self.inter = type("I", (), {})()
        self.inter.user, self.inter.response, self.inter.followup = _User(), _Resp(), _Follow()
        self.inter.client, self.inter.channel = _Client(), (_Chan() if channel else None)


def _patch_pipeline(monkeypatch, mode):
    monkeypatch.setattr(ab.config, "ANALYST_REPLY_MODE", mode)
    monkeypatch.setattr(ab, "gather_symbol_data", lambda s, holding=None: {
        "symbol": s, "charts": {"1d": b"p1", "15m": b"p2"}})
    monkeypatch.setattr(ab, "run_analysis", lambda d: ("analysis", "m"))
    monkeypatch.setattr(ab, "format_reply", lambda s, a, d: ["part 1", "part 2"])


def _run_post(h):
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(ab._run_and_post(h.inter, "NVDA", None))


def test_dm_mode_sends_privately_and_leaves_channel_alone(monkeypatch):
    _patch_pipeline(monkeypatch, "dm")
    h = _DmHarness()
    _run_post(h)
    assert h.dms == [("part 1", 2), ("part 2", 0)]
    assert h.deferred_ephemeral is True
    assert h.channel_posts == [] and h.reposts == []          # nothing public, menu stays put
    assert h.followups == [("📬 **NVDA** analysis sent to your DMs.", True)]


def test_dm_forbidden_falls_back_to_channel(monkeypatch):
    _patch_pipeline(monkeypatch, "dm")
    err = ab.discord.Forbidden(type("R", (), {"status": 403, "reason": "x"})(), "Cannot send")
    h = _DmHarness(dm_error=err)
    _run_post(h)
    assert h.dms == [] and h.channel_posts == [("part 1", 2), ("part 2", 0)]
    assert "Could not DM" in h.followups[0][0] and h.followups[0][1] is True
    assert h.reposts == [h.inter.channel]                      # public post -> menu moves below it


def test_dm_forbidden_without_channel_replies_ephemerally(monkeypatch):
    _patch_pipeline(monkeypatch, "dm")
    err = ab.discord.Forbidden(type("R", (), {"status": 403, "reason": "x"})(), "Cannot send")
    h = _DmHarness(dm_error=err, channel=False)
    _run_post(h)
    assert [f[0] for f in h.followups] == ["part 1", "part 2"] and all(f[1] for f in h.followups)
    assert h.reposts == []


def test_dm_mode_failure_is_ephemeral(monkeypatch):
    _patch_pipeline(monkeypatch, "dm")

    def boom(symbol, holding=None):
        raise RuntimeError("no data")

    monkeypatch.setattr(ab, "gather_symbol_data", boom)
    h = _DmHarness()
    _run_post(h)
    assert "failed" in h.followups[0][0] and h.followups[0][1] is True
    assert h.reposts == []
