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

HASHTAGS: dict[str, list[str]] = {
    "books": [
        "booktok",
        "bookstagram",
        "bookrecommendations",
        "readmorebooks",
    ],
    "success": [
        "success",
        "successmindset",
        "selfimprovement",
        "discipline",
        "motivation",
    ],
    "entrepreneurship": [
        "entrepreneur",
        "entrepreneurship",
        "businessmotivation",
        "startupgrind",
    ],
}

# Reels older than this are not "актуальные" and are skipped.
MAX_AGE_DAYS = int(os.environ.get("REELS_MAX_AGE_DAYS", "7"))
# Posts fetched per hashtag from Apify (affects credit usage — see README).
RESULTS_PER_HASHTAG = int(os.environ.get("REELS_RESULTS_LIMIT", "10"))
# Threshold for the main report section.
VIRAL_VIEWS = 1_000_000
# "Rising" section: minimum current views and views/hour to be listed.
RISING_MIN_VIEWS = int(os.environ.get("REELS_RISING_MIN_VIEWS", "100000"))
RISING_MIN_VPH = int(os.environ.get("REELS_RISING_MIN_VPH", "15000"))
# Keep per-reel history this long.
HISTORY_RETENTION_DAYS = 30

APIFY_ACTOR = os.environ.get("APIFY_ACTOR", "apify~instagram-hashtag-scraper")
APIFY_BASE = "https://api.apify.com/v2"
APIFY_POLL_SECONDS = 15
APIFY_MAX_WAIT_SECONDS = 15 * 60

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports"
DATA_DIR = REPO_ROOT / "data"
HISTORY_FILE = DATA_DIR / "reels_history.json"

# --------------------------------------------------------------------------
# Apify client (stdlib only)
# --------------------------------------------------------------------------


def _http_json(url: str, payload: dict | None = None, timeout: int = 60):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_posts(token: str, hashtags: list[str]) -> list[dict]:
    """Run the Apify hashtag scraper and return raw post items."""
    run_input = {"hashtags": hashtags, "resultsLimit": RESULTS_PER_HASHTAG}
    start_url = (
        f"{APIFY_BASE}/acts/{APIFY_ACTOR}/runs?"
        + urllib.parse.urlencode({"token": token})
    )
    run = _http_json(start_url, run_input)["data"]
    run_id, dataset_id = run["id"], run["defaultDatasetId"]
    print(f"Apify run {run_id} started for {len(hashtags)} hashtags…")

    status_url = (
        f"{APIFY_BASE}/actor-runs/{run_id}?"
        + urllib.parse.urlencode({"token": token})
    )
    waited = 0
    while True:
        time.sleep(APIFY_POLL_SECONDS)
        waited += APIFY_POLL_SECONDS
        status = _http_json(status_url)["data"]["status"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run finished with status {status}")
        if waited > APIFY_MAX_WAIT_SECONDS:
            raise RuntimeError("Apify run took too long, giving up")

    items_url = (
        f"{APIFY_BASE}/datasets/{dataset_id}/items?"
        + urllib.parse.urlencode({"token": token, "clean": "true", "format": "json"})
    )
    items = _http_json(items_url, timeout=120)
    print(f"Apify returned {len(items)} items")

    # The actor may return posts directly, or hashtag objects that embed
    # topPosts/latestPosts — handle both shapes.
    posts: list[dict] = []
    for item in items:
        if "topPosts" in item or "latestPosts" in item:
            posts.extend(item.get("topPosts") or [])
            posts.extend(item.get("latestPosts") or [])
        else:
            posts.append(item)
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
    haystack = " ".join(
        [post.get("caption") or ""] + list(post.get("hashtags") or [])
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


def extract_reel(post: dict, now: datetime) -> dict | None:
    """Normalize a raw Apify post into a reel record, or None to skip."""
    url = post.get("url") or ""
    is_video = (
        post.get("type") == "Video"
        or post.get("productType") in ("clips", "reels", "igtv")
        or "/reel/" in url
    )
    if not is_video:
        return None

    posted = parse_ts(post.get("timestamp"))
    if posted is None or now - posted > timedelta(days=MAX_AGE_DAYS):
        return None

    caption = post.get("caption") or ""
    if not looks_english(caption):
        return None

    views = post.get("videoPlayCount") or post.get("videoViewCount") or 0
    shortcode = post.get("shortCode") or url.rstrip("/").rsplit("/", 1)[-1]
    if not shortcode or not views:
        return None

    age_hours = max((now - posted).total_seconds() / 3600, 0.5)
    return {
        "shortcode": shortcode,
        "url": url or f"https://www.instagram.com/reel/{shortcode}/",
        "author": post.get("ownerUsername") or "?",
        "caption": caption,
        "category": categorize(post),
        "views": int(views),
        "likes": int(post.get("likesCount") or 0),
        "comments": int(post.get("commentsCount") or 0),
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


def build_report(viral: list[dict], rising: list[dict], now: datetime) -> str:
    date_str = now.strftime("%d.%m.%Y")
    lines = [
        f"# 🎬 Утренний отчёт по Instagram Reels — {date_str}",
        "",
        "Темы: книги, успех, предпринимательство (англоязычные ролики).",
        f"Учитываются только свежие ролики — не старше {MAX_AGE_DAYS} дн.",
        f"Данные сняты {now.strftime('%d.%m.%Y %H:%M UTC')}.",
        "",
        f"## 🏆 Пробили 1 млн просмотров — {len(viral)} шт.",
        "",
        "Отсортировано по скорости набора первого миллиона (быстрые сверху).",
        "",
    ]
    if viral:
        lines += [
            "| # | До 1 млн | Точность | Тема | Просмотры | В час | Возраст | Автор | Ролик |",
            "|---|----------|----------|------|-----------|-------|---------|-------|-------|",
        ]
        for i, r in enumerate(viral, 1):
            mark = "✅ замерено" if r["measured"] else "≈ оценка"
            lines.append(
                f"| {i} | **{fmt_hours(r['ttm'])}** | {mark} "
                f"| {CATEGORY_LABELS[r['category']]} | {fmt_views(r['views'])} "
                f"| {fmt_views(r['vph'])} | {fmt_hours(r['age_hours'])} "
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
            "| # | Прогноз до 1 млн | Тема | Просмотры | В час | Возраст | Автор | Ролик |",
            "|---|------------------|------|-----------|-------|---------|-------|-------|",
        ]
        for i, r in enumerate(rising, 1):
            eta = (VIRAL_VIEWS - r["views"]) / max(r["vph"], 1)
            lines.append(
                f"| {i} | ~{fmt_hours(r['age_hours'] + eta)} "
                f"| {CATEGORY_LABELS[r['category']]} | {fmt_views(r['views'])} "
                f"| {fmt_views(r['vph'])} | {fmt_hours(r['age_hours'])} "
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
    try:
        data = _http_json(f"https://api.telegram.org/bot{token}/getUpdates")
        for update in reversed(data.get("result", [])):
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("id"):
                return str(chat["id"])
    except Exception as exc:  # noqa: BLE001
        print(f"getUpdates failed: {exc}", file=sys.stderr)
    return None


def send_telegram(viral: list[dict], now: datetime) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return
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
        lines.append("Быстрее всех до 1 млн:")
        for i, r in enumerate(top, 1):
            mark = "" if r["measured"] else " (≈)"
            lines.append(
                f"{i}. {fmt_hours(r['ttm'])}{mark} · {fmt_views(r['views'])} "
                f"· @{r['author']}\n{r['url']}"
            )
    else:
        lines.append("Свежих роликов с 1 млн+ сегодня нет.")
    lines.append("Полный отчёт — в папке reports репозитория.")
    try:
        _http_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat_id, "text": "\n\n".join(lines)[:4000],
             "disable_web_page_preview": True},
        )
        print("Telegram summary sent")
    except Exception as exc:  # noqa: BLE001 — delivery must not fail the run
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
    all_tags = [t for tags in HASHTAGS.values() for t in tags]
    posts = fetch_posts(token, all_tags)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
