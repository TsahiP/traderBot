"""Map Discord channel (and optional category) names to canonical signal routes."""
import re

_CANONICAL_ROUTES = frozenset({
    f"{asset}_{tf}"
    for asset in ("stock", "crypto")
    for tf in ("1m", "5m", "15m", "30m", "1h", "1d", "1w")
} | {"stock_news", "crypto_news"})

_TF = r"(?:1m|5m|15m|30m|1h|1d|1w)"
_PREFixed = re.compile(rf"^(stock|crypto)[-_]({_TF})$")
_TF_ONLY = re.compile(rf"^({_TF})$")
_NEWS = re.compile(r"^(stock|crypto)[-_]news$")


def _slug(text: str) -> str:
    return re.sub(r"[\s_]+", "-", (text or "").strip().lower())


def _asset_from_category(category_name: str | None) -> str | None:
    cat = (category_name or "").lower()
    if "crypto" in cat:
        return "crypto"
    if "stock" in cat:
        return "stock"
    return None


def parse_channel_route(channel_name: str, category_name: str | None = None) -> str | None:
    """Return canonical route (e.g. stock_1h) or None if the channel is not a signal room."""
    name = _slug(channel_name)
    if not name:
        return None

    news = _NEWS.match(name)
    if news:
        route = f"{news.group(1)}_news"
        return route if route in _CANONICAL_ROUTES else None

    prefixed = _PREFixed.match(name)
    if prefixed:
        asset, tf = prefixed.groups()
        route = f"{asset}_{tf}"
        return route if route in _CANONICAL_ROUTES else None

    tf_only = _TF_ONLY.match(name)
    if tf_only:
        asset = _asset_from_category(category_name)
        if not asset:
            return None
        route = f"{asset}_{tf_only.group(1)}"
        return route if route in _CANONICAL_ROUTES else None

    return None
