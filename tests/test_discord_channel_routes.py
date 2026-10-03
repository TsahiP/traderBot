"""Discord channel name → route parsing."""
import discord_channel_routes as dcr


def test_prefixed_stock_and_crypto_timeframes():
    assert dcr.parse_channel_route("stock-1h") == "stock_1h"
    assert dcr.parse_channel_route("stock_1d") == "stock_1d"
    assert dcr.parse_channel_route("crypto-5m") == "crypto_5m"
    assert dcr.parse_channel_route("CRYPTO 1W") == "crypto_1w"


def test_news_channels():
    assert dcr.parse_channel_route("stock-news") == "stock_news"
    assert dcr.parse_channel_route("crypto_news") == "crypto_news"


def test_category_fallback_for_tf_only():
    assert dcr.parse_channel_route("1h", "Stocks alerts") == "stock_1h"
    assert dcr.parse_channel_route("15m", "Crypto signals") == "crypto_15m"
    assert dcr.parse_channel_route("1h", None) is None
    assert dcr.parse_channel_route("1h", "General chat") is None


def test_unmatched_channels():
    assert dcr.parse_channel_route("misc-chat") is None
    assert dcr.parse_channel_route("stock-bogus") is None
