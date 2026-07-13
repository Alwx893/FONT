#!/usr/bin/env python3
"""
Daily Instagram Reels trend agent.

Collects fresh English-language reels about books, success and
entrepreneurship, tracks how fast each one reached its first million
views, and writes a markdown report sorted by that speed.

Data source: Apify actor `apify/instagram-hashtag-scraper`
(requires APIFY_TOKEN env var).

Instagram does not expose historical view curves, so the agent keeps
its own snapshot history in data/reels_history.json. When a reel is
first seen below 1M views and later crosses it, the crossing time is
interpolated from real measurements ("замерено"). Reels that are
already past 1M when first discovered get an estimate based on their
average views-per-hour since posting ("оценка").

Zero third-party dependencies — Python 3.10+ stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Configuration (override via env vars where noted)
# --------------------------------------------------------------------------

# Primary discovery: Instagram reels search by keyword. True virality
# lives on small accounts, so authors above MAX_FOLLOWERS are dropped —
# a million views on an 18M-follower page is just its audience showing up.
SEARCH_QUERIES: dict[str, list[str]] = {
    "books": [
        "book recommendations",
        "books that changed my life",
    ],
    "success": [
        "self improvement",
        "success mindset",
    ],
    "entrepreneurship": [
        "entrepreneur motivation",
        "how to start a business",
    ],
}

# Optional legacy source (REELS_USE_WATCHLIST=1): reels from big
# English-language accounts per theme. Exempt from the follower cap.
ACCOUNTS: dict[str, list[str]] = {
    "books": [
        "goodreads",
        "epicreads",
        "penguinrandomhouse",
        "readwithjenna",
        "aymansbooks",
        "jackbenedwards",
    ],
    "success": [
        "melrobbins",
        "jayshetty",
        "garyvee",
        "edmylett",
        "simonsinek",
        "lewishowes",
        "thegoodquote",
        "millionaire_mentor",
    ],
    "entrepreneurship": [
        "alexhormozi",
        "leilahormozi",
        "codie_sanchez",
        "grantcardone",
        "patrickbetdavid",
        "danmartell",
        "foundr",
        "entrepreneur",
        "noahkagan",
        "thedankoe",
    ],
}

ACCOUNT_CATEGORY: dict[str, str] = {
    account: category
    for category, accounts in ACCOUNTS.items()
    for account in accounts
}

# Optional extra source: hashtag feeds (recent posts, mostly photos —
# low signal). Enable with REELS_USE_HASHTAGS=1.
HASHTAGS = [
    "booktok", "bookstagram", "successmindset", "selfimprovement",
    "entrepreneur", "businessmotivation",
]

# Reels older than this are not "актуальные" and are skipped.
MAX_AGE_DAYS = int(os.environ.get("REELS_MAX_AGE_DAYS", "7"))
# Authors with more followers than this are dropped: their millions of
# views come from subscribers, not from the algorithm going viral.
MAX_FOLLOWERS = int(os.environ.get("REELS_MAX_FOLLOWERS", "100000"))
# Search depth per query (pages of search results).
SEARCH_PAGES = int(os.environ.get("REELS_SEARCH_PAGES", "2"))
# Reels fetched per watched account (affects credit usage — see README).
REELS_PER_ACCOUNT = int(os.environ.get("REELS_PER_ACCOUNT", "5"))
# Posts fetched per hashtag when REELS_USE_HASHTAGS=1.
RESULTS_PER_HASHTAG = int(os.environ.get("REELS_RESULTS_LIMIT", "10"))
# Threshold for the main report section.
VIRAL_VIEWS = 1_000_000
# "Rising" section: minimum current views and views/hour to be listed.
RISING_MIN_VIEWS = int(os.environ.get("REELS_RISING_MIN_VIEWS", "100000"))
RISING_MIN_VPH = int(os.environ.get("REELS_RISING_MIN_VPH", "15000"))
# Keep per-reel history this long.
HISTORY_RETENTION_DAYS = 30

SEARCH_ACTOR = os.environ.get(
    "APIFY_SEARCH_ACTOR", "patient_discovery~instagram-search-reels"
)
REEL_ACTOR = os.environ.get("APIFY_REEL_ACTOR", "apify~instagram-reel-scraper")
HASHTAG_ACTOR = os.environ.get("APIFY_ACTOR", "apify~instagram-hashtag-scraper")
APIFY_BASE = "https://api.apify.com/v2"
APIFY_POLL_SECONDS = 15
APIFY_MAX_WAIT_SECONDS = 15 * 60

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports"
DATA_DIR = REPO_ROOT / "data"
HISTORY_FILE = DATA_DIR / "reels_history.json"

# Diagnostics written to data/debug_last_run.json on every run, so issues
# can be investigated from the repo without digging into CI logs.
DEBUG: dict = {"filters": {}}

# --------------------------------------------------------------------------
# Apify client (stdlib only)
# --------------------------------------------------------------------------


def _http_json(url: str, payload: dict | None = None, timeout: int = 60,
               token: str | None = None):
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        body = ""
        try:
            body = err.read().decode()[:600]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(
            f"HTTP {err.code} for {url.split('?')[0]}: {body}"
        ) from None


def run_actor(token: str, actor: str, run_input: dict, label: str) -> list[dict]:
    """Start an Apify actor run, wait for it, return dataset items."""
    run = _http_json(f"{APIFY_BASE}/acts/{actor}/runs", run_input,
                     token=token)["data"]
    run_id, dataset_id = run["id"], run["defaultDatasetId"]
    print(f"Apify run {run_id} ({label}) started…")

    status_url = f"{APIFY_BASE}/actor-runs/{run_id}"
    waited = 0
    while True:
        time.sleep(APIFY_POLL_SECONDS)
        waited += APIFY_POLL_SECONDS
        status = _http_json(status_url, token=token)["data"]["status"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {label} finished as {status}")
        if waited > APIFY_MAX_WAIT_SECONDS:
            raise RuntimeError(f"Apify run {label} took too long, giving up")

    items_url = (
        f"{APIFY_BASE}/datasets/{dataset_id}/items?"
        + urllib.parse.urlencode({"clean": "true", "format": "json"})
    )
    items = _http_json(items_url, timeout=120, token=token)
    print(f"Apify {label}: {len(items)} items")
    dbg = DEBUG.setdefault("apify", {})
    dbg[label] = {
        "run_id": run_id,
        "items_count": len(items),
        "item_samples": [
            json.dumps(i, ensure_ascii=False)[:700] for i in items[:2]
        ],
    }
    return items


# Candidate input shapes for the search actor, tried in order until one
# is accepted; the winner is reused for the remaining queries. Rejected
# inputs fail at run creation (HTTP 400) before any credits are spent.
SEARCH_INPUT_VARIANTS = (
    lambda q: {"keyword": q, "maxPages": SEARCH_PAGES},
    lambda q: {"query": q, "maxPages": SEARCH_PAGES},
    lambda q: {"keyword": q},
    lambda q: {"query": q},
    lambda q: {"search": q},
)
_search_variant: int | None = None


def search_reels(token: str, query: str, label: str) -> list[dict]:
    global _search_variant
    order = (
        [_search_variant]
        if _search_variant is not None
        else range(len(SEARCH_INPUT_VARIANTS))
    )
    last_err: Exception | None = None
    for idx in order:
        run_input = SEARCH_INPUT_VARIANTS[idx](query)
        try:
            items = run_actor(token, SEARCH_ACTOR, run_input, label)
            _search_variant = idx
            return items
        except RuntimeError as err:
            last_err = err
            DEBUG.setdefault("search_input_attempts", []).append(
                f"{label} {sorted(run_input)}: {str(err)[:200]}"
            )
            if "HTTP 400" not in str(err):
                raise
    raise last_err  # all input shapes rejected


def fetch_posts(token: str) -> list[dict]:
    """Collect candidates: keyword search + optional legacy sources.

    Each post gets a `_category` hint from the query that found it.
    """
    posts: list[dict] = []
    for category, queries in SEARCH_QUERIES.items():
        for query in queries:
            for item in search_reels(token, query, f"search:{query}"):
                item["_category"] = category
                posts.append(item)

    if os.environ.get("REELS_USE_WATCHLIST") == "1":
        for item in run_actor(
            token,
            REEL_ACTOR,
            {"username": list(ACCOUNT_CATEGORY),
             "resultsLimit": REELS_PER_ACCOUNT},
            "watchlist",
        ):
            item["_watchlist"] = True
            posts.append(item)

    if os.environ.get("REELS_USE_HASHTAGS") == "1":
        items = run_actor(
            token,
            HASHTAG_ACTOR,
            {"hashtags": HASHTAGS, "resultsLimit": RESULTS_PER_HASHTAG},
            "hashtags",
        )
        # That actor may return posts directly, or hashtag objects that
        # embed topPosts/latestPosts — handle both shapes.
        for item in items:
            if "topPosts" in item or "latestPosts" in item:
                posts.extend(item.get("topPosts") or [])
                posts.extend(item.get("latestPosts") or [])
            else:
                posts.append(item)

    DEBUG["posts_total"] = len(posts)
    return posts


# --------------------------------------------------------------------------
# Filtering helpers
# --------------------------------------------------------------------------

EN_STOPWORDS = {
    "the", "and", "you", "your", "this", "that", "how", "what", "when",
    "for", "with", "are", "was", "have", "from", "they", "will", "can",
    "not", "but", "out", "get", "just", "like", "make", "more", "one",
    "about", "who", "why", "did", "been", "them", "then", "than", "some",
    "into", "only", "over", "most", "read", "book", "books", "money",
    "success", "business", "life", "every", "people", "never", "always",
    "start", "stop", "here", "our", "his", "her", "its", "it", "is",
    "to", "of", "in", "on", "at", "by", "be", "do", "if", "we", "my",
}

_CLEAN_RE = re.compile(r"(https?://\S+|[#@]\w+)")


def looks_english(text: str) -> bool:
    """Cheap language check: latin script + English stopwords."""
    text = _CLEAN_RE.sub(" ", text or "")
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True  # no caption to judge by — keep it
    non_latin = sum(1 for c in letters if ord(c) > 0x024F)
    if non_latin / len(letters) > 0.2:
        return False
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if len(words) >= 6:
        return any(w in EN_STOPWORDS for w in words)
    return True


CATEGORY_KEYWORDS = {
    "books": ("book", "read", "novel", "author", "library"),
    "entrepreneurship": (
        "entrepreneur", "business", "startup", "hustle", "founder",
        "marketing", "sales", "ecommerce",
    ),
    "success": (
        "success", "mindset", "motivat", "discipline", "selfimprovement",
        "growth", "wealth", "rich",
    ),
}


def categorize(post: dict) -> str:
    owner = post.get("ownerUsername") or post.get("username") or ""
    if isinstance(owner, dict):
        owner = owner.get("username") or ""
    if owner.lower() in ACCOUNT_CATEGORY:
        return ACCOUNT_CATEGORY[owner.lower()]
    caption = post.get("caption") or ""
    if isinstance(caption, dict):
        caption = caption.get("text") or ""
    haystack = " ".join(
        [caption] + list(post.get("hashtags") or [])
    ).lower()
    for cat, keys in CATEGORY_KEYWORDS.items():
        if any(k in haystack for k in keys):
            return cat
    return "success"


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _skip(reason: str) -> None:
    DEBUG["filters"][reason] = DEBUG["filters"].get(reason, 0) + 1
    return None


def pick(d: dict, *keys, default=None):
    """First non-empty value among several possible field names."""
    for key in keys:
        value = d.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def extract_reel(post: dict, now: datetime) -> dict | None:
    """Normalize a raw post from any of the actors, or None to skip.

    Different actors name the same fields differently (camelCase Apify
    style vs raw Instagram API snake_case), so every field is probed
    under all known aliases.
    """
    owner = pick(post, "user", "owner", default={})
    if not isinstance(owner, dict):
        owner = {}
    url = pick(post, "url", "postUrl", "link", default="")
    shortcode = pick(post, "shortCode", "code", "shortcode") or (
        url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    )
    views = pick(
        post, "videoPlayCount", "playCount", "play_count",
        "videoViewCount", "viewCount", "view_count", default=0,
    )

    is_video = (
        post.get("type") == "Video"
        or post.get("productType") in ("clips", "reels", "igtv")
        or post.get("product_type") in ("clips", "reels")
        or post.get("media_type") == 2
        or "/reel/" in url
        or bool(pick(post, "playCount", "play_count", "videoPlayCount"))
    )
    if not is_video:
        return _skip("not_video")

    posted = parse_ts(
        pick(post, "timestamp", "taken_at", "takenAt", "taken_at_timestamp")
    )
    if posted is None or now - posted > timedelta(days=MAX_AGE_DAYS):
        return _skip("no_timestamp_or_too_old")

    caption = pick(post, "caption", "caption_text", default="")
    if isinstance(caption, dict):
        caption = caption.get("text") or ""
    if not looks_english(caption):
        return _skip("not_english")

    if not shortcode or not views:
        return _skip("no_views_or_shortcode")

    followers = pick(
        owner, "follower_count", "followersCount", "followers",
        default=pick(post, "followersCount", "ownerFollowersCount"),
    )
    if isinstance(followers, dict):  # e.g. edge_followed_by: {"count": N}
        followers = followers.get("count")
    if followers is not None:
        followers = int(followers)

    # The whole point: keep only small accounts, where big view counts
    # mean the algorithm pushed the reel, not the author's own audience.
    if not post.get("_watchlist"):
        if followers is None:
            return _skip("no_followers_data")
        if followers > MAX_FOLLOWERS:
            return _skip("too_many_followers")

    author = (
        pick(post, "ownerUsername")
        or pick(owner, "username", "handle")
        or pick(post, "username")
        or "?"
    )
    age_hours = max((now - posted).total_seconds() / 3600, 0.5)
    views = int(views)
    return {
        "shortcode": shortcode,
        "url": url or f"https://www.instagram.com/reel/{shortcode}/",
        "author": author,
        "caption": caption,
        "category": post.get("_category") or categorize(post),
        "views": views,
        "followers": followers,
        "likes": int(pick(post, "likesCount", "like_count", default=0)),
        "comments": int(pick(post, "commentsCount", "comment_count",
                             default=0)),
        "posted": posted,
        "age_hours": age_hours,
        "vph": int(views / age_hours),
    }


# --------------------------------------------------------------------------
# History: measured time-to-1M
# --------------------------------------------------------------------------


def load_history() -> dict:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return {}


def update_history(history: dict, reels: list[dict], now: datetime) -> None:
    now_iso = now.isoformat()
    for reel in reels:
        entry = history.setdefault(
            reel["shortcode"],
            {"post_ts": reel["posted"].isoformat(), "snapshots": []},
        )
        entry["snapshots"].append([now_iso, reel["views"]])

    cutoff = now - timedelta(days=HISTORY_RETENTION_DAYS)
    for code in list(history):
        post_ts = parse_ts(history[code].get("post_ts"))
        if post_ts and post_ts < cutoff:
            del history[code]


def hours_to_million(reel: dict, history: dict) -> tuple[float, bool] | None:
    """(hours from posting to 1M, measured?) or None if not there yet."""
    entry = history.get(reel["shortcode"], {})
    snapshots = [
        (parse_ts(ts), v) for ts, v in entry.get("snapshots", [])
    ]
    snapshots = sorted((s for s in snapshots if s[0]), key=lambda s: s[0])

    below = [(t, v) for t, v in snapshots if v < VIRAL_VIEWS]
    above = [(t, v) for t, v in snapshots if v >= VIRAL_VIEWS]
    if below and above:
        (t0, v0), (t1, v1) = below[-1], above[0]
        span = (t1 - t0).total_seconds() / 3600
        frac = (VIRAL_VIEWS - v0) / max(v1 - v0, 1)
        crossing = t0 + timedelta(hours=span * frac)
        return (crossing - reel["posted"]).total_seconds() / 3600, True

    if reel["views"] >= VIRAL_VIEWS:
        # Only current total is known — assume steady average pace.
        return reel["age_hours"] * VIRAL_VIEWS / reel["views"], False
    return None


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

CATEGORY_LABELS = {
    "books": "📚 Книги",
    "success": "🏔️ Успех",
    "entrepreneurship": "💼 Бизнес",
}


def fmt_hours(hours: float) -> str:
    if hours < 48:
        return f"{hours:.0f} ч"
    return f"{hours / 24:.1f} дн"


def fmt_views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} млн"
    return f"{n / 1000:.0f} тыс"


def snippet(caption: str, limit: int = 70) -> str:
    text = re.sub(r"\s+", " ", caption).replace("|", "¦").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def fmt_multiplier(reel: dict) -> str:
    if not reel.get("followers"):
        return "—"
    return f"×{reel['views'] / max(reel['followers'], 1):.0f}"


def fmt_followers(reel: dict) -> str:
    return fmt_views(reel["followers"]) if reel.get("followers") else "?"


def build_report(viral: list[dict], rising: list[dict], now: datetime) -> str:
    date_str = now.strftime("%d.%m.%Y")
    lines = [
        f"# 🎬 Утренний отчёт по Instagram Reels — {date_str}",
        "",
        "Темы: книги, успех, предпринимательство (англоязычные ролики).",
        f"Только авторы до {fmt_views(MAX_FOLLOWERS)} подписчиков — ловим ролики,",
        "которые разогнал алгоритм, а не собственная аудитория канала.",
        f"Только свежие ролики — не старше {MAX_AGE_DAYS} дн. "
        f"«×аудитория» — во сколько раз просмотры превысили число подписчиков.",
        f"Данные сняты {now.strftime('%d.%m.%Y %H:%M UTC')}.",
        "",
        f"## 🏆 Пробили 1 млн просмотров — {len(viral)} шт.",
        "",
        "Отсортировано по скорости набора первого миллиона (быстрые сверху).",
        "",
    ]
    if viral:
        lines += [
            "| # | До 1 млн | Точность | Тема | Просмотры | Подписчики | ×аудитория | Автор | Ролик |",
            "|---|----------|----------|------|-----------|------------|------------|-------|-------|",
        ]
        for i, r in enumerate(viral, 1):
            mark = "✅ замерено" if r["measured"] else "≈ оценка"
            lines.append(
                f"| {i} | **{fmt_hours(r['ttm'])}** | {mark} "
                f"| {CATEGORY_LABELS[r['category']]} | {fmt_views(r['views'])} "
                f"| {fmt_followers(r)} | **{fmt_multiplier(r)}** "
                f"| @{r['author']} | [{snippet(r['caption'], 45) or r['shortcode']}]({r['url']}) |"
            )
    else:
        lines.append("_Сегодня свежих роликов с 1 млн+ не найдено._")

    lines += [
        "",
        f"## 🚀 На подлёте к миллиону — {len(rising)} шт.",
        "",
        f"Ролики моложе {MAX_AGE_DAYS} дн. с {fmt_views(RISING_MIN_VIEWS)}+ просмотров "
        f"и темпом от {fmt_views(RISING_MIN_VPH)}/час. Прогноз — при сохранении темпа.",
        "",
    ]
    if rising:
        lines += [
            "| # | Прогноз до 1 млн | Тема | Просмотры | Подписчики | ×аудитория | В час | Автор | Ролик |",
            "|---|------------------|------|-----------|------------|------------|-------|-------|-------|",
        ]
        for i, r in enumerate(rising, 1):
            eta = (VIRAL_VIEWS - r["views"]) / max(r["vph"], 1)
            lines.append(
                f"| {i} | ~{fmt_hours(r['age_hours'] + eta)} "
                f"| {CATEGORY_LABELS[r['category']]} | {fmt_views(r['views'])} "
                f"| {fmt_followers(r)} | {fmt_multiplier(r)} "
                f"| {fmt_views(r['vph'])} "
                f"| @{r['author']} | [{snippet(r['caption'], 45) or r['shortcode']}]({r['url']}) |"
            )
    else:
        lines.append("_Кандидатов не найдено._")

    lines += [
        "",
        "---",
        "",
        "**Методика.** Instagram не публикует историю просмотров, поэтому агент",
        "ежедневно снимает показания и хранит их в `data/reels_history.json`.",
        "«✅ замерено» — момент пересечения 1 млн зафиксирован между двумя",
        "реальными замерами (интерполяция). «≈ оценка» — ролик уже был за",
        "миллионом при первом обнаружении; время рассчитано по средней",
        "скорости с момента публикации. Точность растёт с каждым днём работы.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Telegram (optional)
# --------------------------------------------------------------------------


CHAT_ID_FILE = DATA_DIR / "telegram_chat_id.txt"


def discover_chat_id(token: str) -> str | None:
    """Find the chat id from the bot's recent updates (user must have
    messaged the bot at least once in the last 24h)."""
    tg_debug = DEBUG.setdefault("telegram", {})
    try:
        data = _http_json(f"https://api.telegram.org/bot{token}/getUpdates")
        updates = data.get("result", [])
        tg_debug["updates_count"] = len(updates)
        for update in reversed(updates):
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("id"):
                return str(chat["id"])
    except Exception as exc:  # noqa: BLE001
        tg_debug["getUpdates_error"] = str(exc)[:300]
        print(f"getUpdates failed: {exc}", file=sys.stderr)
    return None


def send_telegram(viral: list[dict], now: datetime) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    tg_debug = DEBUG.setdefault("telegram", {})
    try:
        me = _http_json(f"https://api.telegram.org/bot{token}/getMe")
        tg_debug["bot_username"] = (me.get("result") or {}).get("username")
    except Exception as exc:  # noqa: BLE001
        tg_debug["getMe_error"] = str(exc)[:300]
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id and CHAT_ID_FILE.exists():
        chat_id = CHAT_ID_FILE.read_text().strip()
    if not chat_id:
        chat_id = discover_chat_id(token)
        if chat_id:
            CHAT_ID_FILE.write_text(chat_id)
            print(f"Discovered Telegram chat id {chat_id}")
    if not chat_id:
        print(
            "Telegram: chat id unknown — send any message to your bot "
            "and the next run will pick it up.",
            file=sys.stderr,
        )
        return
    top = viral[:5]
    lines = [f"🎬 Reels-отчёт {now.strftime('%d.%m.%Y')}"]
    if top:
        lines.append("Быстрее всех до 1 млн (авторы < "
                     f"{fmt_views(MAX_FOLLOWERS)} подписчиков):")
        for i, r in enumerate(top, 1):
            mark = "" if r["measured"] else " (≈)"
            lines.append(
                f"{i}. {fmt_hours(r['ttm'])}{mark} · {fmt_views(r['views'])} "
                f"({fmt_multiplier(r)} к аудитории) · @{r['author']}\n{r['url']}"
            )
    else:
        lines.append("Свежих роликов с 1 млн+ сегодня нет.")
    lines.append("Полный отчёт — в папке reports репозитория.")
    tg_debug = DEBUG.setdefault("telegram", {})
    tg_debug["chat_id"] = chat_id
    try:
        _http_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat_id, "text": "\n\n".join(lines)[:4000],
             "disable_web_page_preview": True},
        )
        tg_debug["sent"] = True
        print("Telegram summary sent")
    except Exception as exc:  # noqa: BLE001 — delivery must not fail the run
        tg_debug["send_error"] = str(exc)[:300]
        print(f"Telegram send failed: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print(
            "ERROR: APIFY_TOKEN is not set. Create a free token at "
            "https://console.apify.com/settings/integrations and add it "
            "as a repository secret named APIFY_TOKEN.",
            file=sys.stderr,
        )
        return 1

    now = datetime.now(timezone.utc)
    posts = fetch_posts(token)

    reels_by_code: dict[str, dict] = {}
    for post in posts:
        reel = extract_reel(post, now)
        if reel:
            reels_by_code.setdefault(reel["shortcode"], reel)
    reels = list(reels_by_code.values())
    print(f"{len(reels)} fresh English reels after filtering")

    history = load_history()
    update_history(history, reels, now)

    viral, rising = [], []
    for reel in reels:
        ttm = hours_to_million(reel, history)
        if ttm is not None:
            reel["ttm"], reel["measured"] = ttm
            viral.append(reel)
        elif reel["views"] >= RISING_MIN_VIEWS and reel["vph"] >= RISING_MIN_VPH:
            rising.append(reel)
    viral.sort(key=lambda r: r["ttm"])
    rising.sort(key=lambda r: r["vph"], reverse=True)

    report = build_report(viral, rising, now)
    REPORTS_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"{now.strftime('%Y-%m-%d')}.md"
    report_path.write_text(report)
    (REPORTS_DIR / "latest.md").write_text(report)
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1))
    print(f"Report written to {report_path}")

    send_telegram(viral, now)

    DEBUG["reels_kept"] = len(reels)
    DEBUG["run_at"] = now.isoformat()
    (DATA_DIR / "debug_last_run.json").write_text(
        json.dumps(DEBUG, ensure_ascii=False, indent=1)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
