"""Discord analyst room: locked channel + ticker select menu -> local LLM analysis.

Run in its own terminal:  python analystbot.py

Behavior:
  - Ensures a text channel (DISCORD_ANALYST_CHANNEL, default "fundi-analyst")
    exists in the guild, locks typing for @everyone, and posts the ticker menu.
    The menu refreshes itself from output/signal_config.json watchlists.
  - A user picks a ticker: a small modal asks for an optional position
    (shares held / average price / dollar risk; blanks = new cash entry).
    The bot then gathers the funda snapshot, news, signal history, macro
    context, SMC + squeeze reads and the 5-step trade plan, renders 1D/15M
    chart PNGs, and runs the local LLM (LM Studio, model from LLM_MODEL env)
    with the charts attached as images (ANALYST_VISION, analysis falls back
    to text-only if the server rejects them). The analysis posts as chunked
    messages plus the chart files. A per-user cooldown blocks spam.

Permissions the bot needs: View Channels, Send Messages, Manage Channels
(create + lock the room), Manage Messages (pin the menu).

Logs: logs/analystbot.log. Stop with Ctrl+C.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

import config
import signals
from analyst_context import format_reply, gather_symbol_data, run_analysis
from market_symbols import is_bybit_crypto

import discord
from discord.ext import commands

NY = ZoneInfo("America/New_York")

logger = logging.getLogger("analystbot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_handler = RotatingFileHandler(
    config.LOG_DIR / "analystbot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)

MENU_INTRO = (
    "**🔎 Fundi analyst room**\n"
    "Pick a ticker below — the bot pulls everything it has on that stock "
    "(fundamentals snapshot, recent news, signal history), runs the 5-step "
    "protocol through the local LLM (catalysts, macro, Smart Money, Squeeze "
    "Momentum, execution plan) and posts the briefing with chart images. "
    "Free typing is disabled in this room."
)


def bot_token() -> str:
    from dotenv import load_dotenv as _load

    _load(config.BASE_DIR / ".env")
    tok = (os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or "").strip()
    if not tok:
        raise SystemExit("DISCORD_BOT_TOKEN (or DISCORD_TOKEN) missing in .env")
    return tok


def menu_symbols(cfg: dict) -> list[str]:
    """Stock tickers only, deduped, sorted; crypto excluded."""
    out: list[str] = []
    for watchlist in cfg.get("lists", []):
        for raw in watchlist.get("symbols", []):
            symbol = str(raw).strip().upper()
            if not symbol or symbol in out or is_bybit_crypto(symbol):
                continue
            out.append(symbol)
    return sorted(out)


def parse_holding(qty_raw: str | None, avg_raw: str | None,
                  risk_raw: str | None) -> tuple[dict | None, str | None]:
    """Modal inputs -> (holding dict, error message). Values must be positive."""
    def _num(text: str | None) -> float | None:
        text = (text or "").strip()
        if not text:
            return None
        try:
            return float(text.replace(",", "").replace("$", ""))
        except ValueError:
            return None

    qty, avg, risk = _num(qty_raw), _num(avg_raw), _num(risk_raw)
    if (qty is None) != (avg is None):
        return None, "⚠️ Shares and average price must be given together (or both blank)."
    if qty is not None and qty <= 0:
        return None, "⚠️ Shares must be a positive number."
    if avg is not None and avg <= 0:
        return None, "⚠️ Average price must be a positive number."
    if risk_raw and risk_raw.strip() and (risk is None or risk <= 0):
        return None, "⚠️ Dollar risk must be a positive number."
    if qty is not None:
        return {"qty": qty, "avg_price": avg, "risk_usd": risk}, None
    if risk is not None:
        return {"qty": None, "avg_price": None, "risk_usd": risk}, None
    return None, None


def _chart_files(symbol: str, data: dict) -> list[discord.File]:
    return [
        discord.File(io.BytesIO(png), filename=f"{symbol}_{tf}.png")
        for tf, png in (data.get("charts") or {}).items()
        if png
    ]


async def _run_and_post(interaction: discord.Interaction, symbol: str,
                        holding: dict | None) -> None:
    """Shared flow: defer -> gather -> LLM -> chunked reply (+ chart files)."""
    bot: AnalystBot = interaction.client  # type: ignore[assignment]
    bot.inflight.add(symbol)
    try:
        await interaction.response.defer(thinking=True)
        data = await asyncio.to_thread(gather_symbol_data, symbol, holding=holding)
        files = _chart_files(symbol, data)
        analysis, _model = await asyncio.to_thread(run_analysis, data)
        chunks = format_reply(symbol, analysis, data)
        await interaction.followup.send(chunks[0], files=files)
        for extra in chunks[1:]:
            await interaction.followup.send(extra)
    except Exception as exc:  # noqa: BLE001 — user-visible error must post
        logger.exception("analysis failed for %s", symbol)
        detail = str(exc)[:200]
        try:
            await interaction.followup.send(
                f"⚠️ Analysis for **{symbol}** failed: `{detail}`\n"
                f"Is LM Studio running at `{config.LLM_BASE_URL}` with the model loaded?"
            )
        except Exception:  # noqa: BLE001 — followup window may be gone
            logger.warning("could not deliver failure followup for %s", symbol)
    finally:
        bot.inflight.discard(symbol)


class HoldingModal(discord.ui.Modal):
    """Optional position info: shares held, average price, dollar risk."""

    def __init__(self, symbol: str):
        super().__init__(title=f"Position info — {symbol} (optional)")
        self.symbol = symbol
        self.shares = discord.ui.TextInput(
            label="Shares held (blank = new cash entry)",
            required=False, max_length=12, placeholder="e.g. 50",
        )
        self.avg = discord.ui.TextInput(
            label="Average price (with shares)",
            required=False, max_length=12, placeholder="e.g. 105.20",
        )
        self.risk = discord.ui.TextInput(
            label="Dollar risk per trade",
            required=False, max_length=12, default=str(config.ANALYST_RISK_USD),
            placeholder=str(config.ANALYST_RISK_USD),
        )
        for item in (self.shares, self.avg, self.risk):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        holding, error = parse_holding(self.shares.value, self.avg.value, self.risk.value)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await _run_and_post(interaction, self.symbol, holding)


class AnalystSelect(discord.ui.Select):
    def __init__(self, symbols: list[str], row: int = 0):
        super().__init__(
            custom_id=f"fundi_analyst_select_{row}",
            placeholder="Choose a ticker to analyze…",
            options=[
                discord.SelectOption(label=symbol, value=symbol)
                for symbol in symbols[:25]
            ],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        symbol = self.values[0]
        bot: AnalystBot = interaction.client  # type: ignore[assignment]
        now = time.monotonic()
        last = bot.cooldown.get(interaction.user.id, 0.0)
        wait = int(config.ANALYST_COOLDOWN_S - (now - last))
        if wait > 0:
            await interaction.response.send_message(
                f"⏳ Cooldown: try again in {wait} s.", ephemeral=True
            )
            return
        if symbol in bot.inflight:
            await interaction.response.send_message(
                f"ℹ️ **{symbol}** is already being analyzed — hang on.",
                ephemeral=True,
            )
            return
        bot.cooldown[interaction.user.id] = now
        await interaction.response.send_modal(HoldingModal(symbol))


class AnalystView(discord.ui.View):
    """Persistent select view; options refreshed by editing the menu message."""

    def __init__(self):
        super().__init__(timeout=None)
        self.symbols: list[str] = []

    def refresh_symbols(self, symbols: list[str]) -> bool:
        """Swap in fresh options. Returns True if the option set changed."""
        if self.symbols == symbols:
            return False
        self.symbols = list(symbols)
        self.clear_items()
        if symbols:
            for row in range(0, len(symbols), 25):
                self.add_item(AnalystSelect(symbols[row: row + 25], row // 25))
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.exception("analyst view error on %s: %s", getattr(item, "custom_id", "?"), error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(f"⚠️ Analyst error: `{error}`", ephemeral=True)
            else:
                await interaction.response.send_message(
                    f"⚠️ Analyst error: `{error}`", ephemeral=True
                )
        except Exception:  # noqa: BLE001
            pass


class AnalystBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True  # channel discovery; no privileged intents needed
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.view = AnalystView()
        self.menu_message: discord.Message | None = None
        self.cooldown: dict[int, float] = {}
        self.inflight: set[str] = set()
        self._watchlist_task: asyncio.Task | None = None
        self._view_registered = False
        self._perm_attempted = False

    async def ensure_channel(self) -> discord.TextChannel:
        guild = next(iter(self.guilds), None)
        if guild is None:
            raise SystemExit("bot is not in any guild — invite it first")
        name = config.ANALYST_CHANNEL_NAME.lower().strip()
        channel = discord.utils.get(guild.text_channels, name=name)
        if channel is None:
            channel = await guild.create_text_channel(name)
            logger.info("created channel #%s in %s", name, guild.name)
        if self._perm_attempted:
            return channel
        existing = channel.overwrites_for(guild.default_role)
        if existing.send_messages is False and existing.create_public_threads is False:
            self._perm_attempted = True  # already locked - nothing to do
            return channel
        self._perm_attempted = True  # try once per session
        try:
            everyone_perms = channel.overwrites_for(guild.default_role)
            everyone_perms.send_messages = False
            everyone_perms.send_messages_in_threads = False
            everyone_perms.create_public_threads = False
            await channel.set_permissions(guild.default_role, overwrite=everyone_perms)
            me_perms = channel.overwrites_for(guild.me)
            me_perms.send_messages = True
            me_perms.read_messages = True
            await channel.set_permissions(guild.me, overwrite=me_perms)
            logger.info("locked #%s against member typing", name)
        except discord.Forbidden:
            logger.warning(
                "cannot lock #%s - grant the bot Manage Roles (or deny @everyone "
                "'Send Messages' in the channel settings)",
                name,
            )
        return channel

    async def sync_menu(self, channel: discord.TextChannel, *, force: bool = False) -> None:
        symbols = menu_symbols(signals.load_config())
        changed = self.view.refresh_symbols(symbols)
        if self.menu_message is None:
            try:
                async for msg in channel.history(limit=25):
                    if msg.author == self.user and msg.components:
                        self.menu_message = msg
                        break
            except discord.HTTPException:
                pass
        if not symbols:
            fresh_intro = MENU_INTRO + "\n*(no stock tickers configured — add watchlists on the Signals dashboard tab)*"
            if self.menu_message is None:
                self.menu_message = await channel.send(fresh_intro)
            elif changed or force:
                await self.menu_message.edit(content=fresh_intro, view=None)
            return
        if self.menu_message is None:
            self.menu_message = await channel.send(MENU_INTRO, view=self.view)
            try:
                await self.menu_message.pin(reason="analyst ticker menu")
            except discord.HTTPException:
                pass
            logger.info("posted ticker menu (%d symbols)", len(symbols))
        elif changed or force:
            await self.menu_message.edit(content=MENU_INTRO, view=self.view)
            logger.info("updated ticker menu (%d symbols)", len(symbols))
        if not self._view_registered and self.view.children:
            # re-attach callbacks for the persisted custom_ids (survives restarts)
            self.add_view(self.view)
            self._view_registered = True
            logger.info("registered persistent analyst view (%d rows)", len(self.view.children))

    async def watchlist_loop(self):
        await self.wait_until_ready()
        interval = max(60.0, float(config.ANALYST_MENU_REFRESH_MIN) * 60)
        while not self.is_closed():
            await asyncio.sleep(interval)
            try:
                channel = await self.ensure_channel()
                await self.sync_menu(channel)
            except Exception as exc:  # noqa: BLE001
                logger.warning("menu sync failed: %s", exc)

    async def on_ready(self):
        logger.info("analystbot ready as %s (guilds: %s)",
                    self.user, [g.name for g in self.guilds])
        try:
            channel = await self.ensure_channel()
            await self.sync_menu(channel, force=True)
        except discord.Forbidden:
            logger.error("missing permissions to setup the analyst room — check bot invite")
        except Exception:  # noqa: BLE001
            logger.exception("analyst room bootstrap failed")
        if self._watchlist_task is None or self._watchlist_task.done():
            self._watchlist_task = asyncio.create_task(self.watchlist_loop())

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        data = interaction.data or {}
        store = self._connection._view_store
        reg = {
            msg_id: [f"{t}|{cid}" for (t, cid) in items]
            for msg_id, items in store._views.items()
        }
        logger.info(
            "interaction recv: type=%s custom_id=%s message=%s | store=%s",
            interaction.type,
            data.get("custom_id"),
            interaction.message.id if interaction.message else None,
            reg,
        )


def main() -> None:
    token = bot_token()

    async def _runner() -> None:
        # the view must be created inside a running loop: this discord.py fork
        # leaves View.__stopped as None in sync context, which silently discards
        # every component interaction ("bot didn't respond in time")
        bot = AnalystBot()
        async with bot:
            await bot.start(token)

    asyncio.run(_runner())


if __name__ == "__main__":
    main()
