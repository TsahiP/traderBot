"""Read a news article from its link and summarize it with the local LLM (LM Studio).

fundabot calls summary_message(item) per story and only sends stories that come
back with a summary. None means the LLM failed; fundabot keeps the story queued
for the next pass. An unreadable article (blocked page, too little text, SEC
filing) is summarized from its headline and provider blurb instead.

Link handling:
  - Finnhub / publisher links: plain GET, redirects followed.
  - news.google.com/rss/articles/<id>: the link is an opaque redirect page, so the
    publisher URL is resolved through Google's batchexecute endpoint first.
  - sec.gov filings are skipped (8-K items already carry tags + a summary).
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote, urlparse

import config
from llm_client import llm_chat_ex

logger = logging.getLogger("fundabot")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
HTTP_TIMEOUT = 15
MIN_ARTICLE_CHARS = 300      # below this the page was a paywall/redirect shell
MAX_PAGE_BYTES = 5_000_000
MAX_SUMMARY_CHARS = 1200     # keeps the whole Discord message under its 2000 limit
SKIP_HOSTS = ("sec.gov",)

SYSTEM_PROMPT = (
    "You are a financial news analyst writing for traders. Summarize the article "
    "in {lang}. Output exactly: 2-3 short bullet lines starting with '• ', then one "
    "final line 'השפעה: <חיובית/שלילית/ניטרלית> על {symbol} — <one short reason>'. "
    "Keep tickers, company names and numbers as written. Use ONLY facts from the "
    "article; if the article is not about the symbol, say so in the last line. "
    "No preamble, no markdown headings."
)


# ------------------------------------------------------------------ article fetch

def decode_google_url(url: str) -> str | None:
    """news.google.com/rss/articles/<id> -> the publisher URL, or None."""
    import requests
    from bs4 import BeautifulSoup

    gid = urlparse(url).path.rstrip("/").split("/")[-1]
    try:
        page = requests.get(f"https://news.google.com/articles/{gid}", headers=UA, timeout=HTTP_TIMEOUT)
        node = BeautifulSoup(page.text, "html.parser").select_one("c-wiz > div[jscontroller]")
        if not node:
            return None
        sig, ts = node.get("data-n-a-sg"), node.get("data-n-a-ts")
        inner = [
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            gid, int(ts), sig,
        ]
        body = "f.req=" + quote(json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]]))
        r = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            data=body,
            headers={**UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=HTTP_TIMEOUT,
        )
        payload = json.loads(r.text.split("\n\n")[1])
        real = json.loads(payload[0][2])[1]
        return real if isinstance(real, str) and real.startswith("http") else None
    except Exception as exc:  # noqa: BLE001 — Google changes this format; fall back quietly
        logger.debug("google url decode failed: %s", exc)
        return None


def extract_text(html: str) -> str:
    """Article paragraphs from an HTML page (prefers <article>, drops page chrome)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()
    root = soup.find("article") or soup
    paras = [p.get_text(" ", strip=True) for p in root.find_all("p")]
    text = "\n".join(p for p in paras if len(p) > 60)
    if len(text) < MIN_ARTICLE_CHARS and root is not soup:  # <article> held only a teaser
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = "\n".join(p for p in paras if len(p) > 60)
    return text


def fetch_article_text(url: str) -> str:
    """Readable article text for a news link, or '' when it can't be read."""
    import requests

    if "news.google.com" in urlparse(url).netloc:
        url = decode_google_url(url) or ""
    if not url or any(h in urlparse(url).netloc for h in SKIP_HOSTS):
        return ""
    try:
        r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        logger.debug("article fetch failed %s: %s", url[:80], exc)
        return ""
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or "html" not in ctype or len(r.content) > MAX_PAGE_BYTES:
        return ""
    if any(h in urlparse(r.url).netloc for h in SKIP_HOSTS):
        return ""
    text = extract_text(r.text)
    return text if len(text) >= MIN_ARTICLE_CHARS else ""


# ------------------------------------------------------------------ LLM summary

def llm_up() -> bool:
    """Cheap check that LM Studio answers, so a pass doesn't pay N timeouts."""
    import requests

    try:
        return requests.get(f"{config.LLM_BASE_URL}/models", timeout=3).ok
    except requests.RequestException:
        return False


def summarize_article(item: dict, text: str) -> str | None:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT.format(lang=config.NEWS_SUMMARY_LANG, symbol=item["symbol"])},
        {"role": "user", "content": (
            f"Symbol: {item['symbol']}\nTitle: {item['title']}\n\n"
            f"Article:\n{text[: config.NEWS_ARTICLE_MAX_CHARS]}"
        )},
    ]
    try:
        out, meta = llm_chat_ex(
            msgs,
            temperature=0.2,
            max_tokens=500,
            timeout=config.NEWS_SUMMARY_TIMEOUT,
            reasoning_effort="none",
            model=config.NEWS_LLM_MODEL or None,
            preload=True,
        )
    except Exception as exc:  # noqa: BLE001 — never let the LLM break the news pass
        logger.warning("news summary LLM failed: %s", exc)
        return None
    out = out.strip()
    if not out or meta.get("finish_reason") == "length":
        return None
    return out[:MAX_SUMMARY_CHARS]


def headline_text(item: dict) -> str:
    """What the model reads when the article itself can't be fetched."""
    blurb = (item.get("summary") or "").strip()
    return f"Headline and provider blurb only (article not readable):\n{item['title']}\n{blurb}".rstrip()


def summary_message(item: dict) -> str | None:
    """Discord message with an LLM summary in Hebrew, or None when the LLM failed."""
    text = fetch_article_text(item["url"]) or headline_text(item)
    summary = summarize_article(item, text)
    if not summary:
        return None
    # RLM at the start of each line keeps Discord laying it out right-to-left,
    # even when the model opens a line with a Latin word or ticker.
    body = "\n".join("‏" + line for line in summary.splitlines() if line.strip())
    return f"**{item['symbol']}**\n{body}\n{item['url']}"
