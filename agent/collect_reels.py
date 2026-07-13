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

import html
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
        "booktok",
    ],
    "success": [
        "self improvement",
        "success mindset",
        "morning routine",
    ],
    "entrepreneurship": [
        "entrepreneur motivation",
        "how to start a business",
        "make money online",
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
MAX_FOLLOWERS = int(os.environ.get("REELS_MAX_FOLLOWERS", "1000000"))
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
RISING_MIN_VPH = int(os.environ.get("REELS_RISING_MIN_VPH", "10000"))
# Candidates from history with at least this many views are re-checked
# daily while they are younger than MAX_AGE_DAYS, so their view curve
# keeps accumulating even when the day's search misses them.
TRACK_MIN_VIEWS = int(os.environ.get("REELS_TRACK_MIN_VIEWS", "50000"))
# Keep per-reel history this long.
HISTORY_RETENTION_DAYS = 30

SEARCH_ACTOR = os.environ.get(
    "APIFY_SEARCH_ACTOR", "patient_discovery~instagram-search-reels"
)
PROFILE_ACTOR = os.environ.get(
    "APIFY_PROFILE_ACTOR", "apify~instagram-profile-scraper"
)
DETAIL_ACTOR = os.environ.get("APIFY_DETAIL_ACTOR", "apify~instagram-scraper")
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


# The exact input field names differ between community actors, so the
# schema is discovered at runtime: Apify exposes each actor's example
# input, and we substitute our query into the field the actor itself
# demonstrates. Guessed variants remain as a fallback.
SEARCH_INPUT_VARIANTS = (
    lambda q: {"keyword": q, "maxPages": SEARCH_PAGES},
    lambda q: {"query": q, "maxPages": SEARCH_PAGES},
    lambda q: {"search": q},
)
_QUERY_KEY_RE = re.compile(r"(keyword|query|search|term|^q$)", re.I)
_PAGES_KEY_RE = re.compile(r"^(max_?pages?|pages?)$", re.I)
_LIMIT_KEY_RE = re.compile(r"^(max_?(items|results)|results?_?limit|limit|count)$", re.I)


def resolve_search_template(token: str) -> dict | None:
    """Recover the search actor's real input fields.

    Tries a meaningful exampleRunInput first; some actors publish a
    placeholder there ({"helloWorld": 123}), so the authoritative
    fallback is the input schema of the actor's latest build, from
    which a template is assembled out of prefill/default values.
    """
    try:
        actor = _http_json(f"{APIFY_BASE}/acts/{SEARCH_ACTOR}",
                           token=token)["data"]
    except Exception as exc:  # noqa: BLE001
        DEBUG["search_template_error"] = str(exc)[:300]
        return None

    body = (actor.get("exampleRunInput") or {}).get("body")
    try:
        example = json.loads(body) if body else None
    except ValueError:
        example = None
    if isinstance(example, dict) and any(
        isinstance(v, str) and v for v in example.values()
    ):
        DEBUG["search_input_template"] = example
        return example

    build_id = ((actor.get("taggedBuilds") or {}).get("latest") or {}) \
        .get("buildId")
    if not build_id:
        DEBUG["search_schema_error"] = "no latest buildId in actor meta"
        return None
    try:
        build = _http_json(f"{APIFY_BASE}/actor-builds/{build_id}",
                           token=token)["data"]
        schema = build.get("inputSchema")
        if isinstance(schema, str):
            schema = json.loads(schema)
        props = (schema or {}).get("properties") or {}
    except Exception as exc:  # noqa: BLE001
        DEBUG["search_schema_error"] = str(exc)[:300]
        return None

    DEBUG["search_schema_keys"] = {
        key: spec.get("type") for key, spec in props.items()
    }
    template: dict = {}
    for key, spec in props.items():
        value = spec.get("prefill", spec.get("default"))
        if value is None and spec.get("type") == "string":
            value = ""
        if value is not None:
            template[key] = value
    if template:
        DEBUG["search_input_template"] = template
        return template
    return None


def build_search_input(template: dict, query: str) -> dict:
    run_input = dict(template)
    query_key = next(
        (k for k, v in template.items()
         if isinstance(v, str) and _QUERY_KEY_RE.search(k)),
        None,
    ) or next(
        (k for k, v in template.items()
         if isinstance(v, str) and not v.startswith("http")),
        "keyword",
    )
    run_input[query_key] = query
    for key, value in template.items():
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if _PAGES_KEY_RE.match(key):
            run_input[key] = SEARCH_PAGES
        elif _LIMIT_KEY_RE.match(key):
            run_input[key] = SEARCH_PAGES * 20
    return run_input


_search_template: dict | None = None
_template_resolved = False


def search_reels(token: str, query: str, label: str) -> list[dict]:
    global _search_template, _template_resolved
    if not _template_resolved:
        _search_template = resolve_search_template(token)
        _template_resolved = True

    attempts: list[dict] = []
    if _search_template is not None:
        attempts.append(build_search_input(_search_template, query))
    attempts.extend(make(query) for make in SEARCH_INPUT_VARIANTS)

    last_err: Exception | None = None
    for run_input in attempts:
        try:
            items = run_actor(token, SEARCH_ACTOR, run_input, label)
            DEBUG.setdefault("search_inputs_used", {})[label] = run_input
            return items
        except RuntimeError as err:
            last_err = err
            DEBUG.setdefault("search_input_attempts", []).append(
                f"{label} {sorted(run_input)}: {str(err)[:200]}"
            )
            if "HTTP 400" not in str(err):
                raise
    raise last_err  # all input shapes rejected


def enrich_followers(token: str, reels: list[dict]) -> None:
    """Fill in missing follower counts via the official profile scraper."""
    missing = sorted({
        r["author"] for r in reels
        if r["followers"] is None and r["author"] != "?"
    })
    if not missing:
        return
    try:
        items = run_actor(token, PROFILE_ACTOR,
                          {"usernames": missing}, "profiles")
    except RuntimeError as err:
        DEBUG["profile_enrich_error"] = str(err)[:300]
        return
    followers_by_user: dict[str, int] = {}
    for item in items:
        name = pick(item, "username", "userName")
        count = pick(item, "followersCount", "follower_count", "followers")
        if isinstance(count, dict):
            count = count.get("count")
        if name and count is not None:
            followers_by_user[str(name).lower()] = int(count)
    for reel in reels:
        if reel["followers"] is None:
            reel["followers"] = followers_by_user.get(reel["author"].lower())


def fetch_posts(token: str) -> list[dict]:
    """Collect candidates: keyword search + optional legacy sources.

    Each post gets a `_category` hint from the query that found it.
    """
    posts: list[dict] = []
    first_ids: set = set()
    answered = 0
    plan = [(cat, q) for cat, queries in SEARCH_QUERIES.items()
            for q in queries]
    for category, query in plan:
        items = search_reels(token, query, f"search:{query}")
        if items:
            answered += 1
            first_ids.add(str(pick(items[0], "id", "pk", "code")))
        for item in items:
            item["_category"] = category
            posts.append(item)
        # Identical first results across different queries mean the
        # actor ignores the query — flag it and stop burning credits.
        if answered >= 2 and len(first_ids) == 1:
            DEBUG["search_query_ignored"] = True
            break

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
        "watchlist": bool(post.get("_watchlist")),
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
        entry["category"] = reel["category"]
        entry["author"] = reel["author"]
        if reel["followers"] is not None:
            entry["followers"] = reel["followers"]
        entry["snapshots"].append([now_iso, reel["views"]])

    cutoff = now - timedelta(days=HISTORY_RETENTION_DAYS)
    for code in list(history):
        post_ts = parse_ts(history[code].get("post_ts"))
        if post_ts and post_ts < cutoff:
            del history[code]


def refetch_tracked(token: str, history: dict, known_codes: set,
                    now: datetime) -> list[dict]:
    """Re-check recent viral candidates missed by today's search.

    Builds the real view curve day by day, which is what turns
    time-to-1M from an estimate into a measurement.
    """
    urls, meta = [], {}
    for code, entry in history.items():
        if code in known_codes:
            continue
        post_ts = parse_ts(entry.get("post_ts"))
        if not post_ts or now - post_ts > timedelta(days=MAX_AGE_DAYS):
            continue
        snapshots = entry.get("snapshots") or []
        if not snapshots or snapshots[-1][1] < TRACK_MIN_VIEWS:
            continue
        urls.append(f"https://www.instagram.com/reel/{code}/")
        meta[code] = entry
    if not urls:
        return []
    try:
        items = run_actor(token, DETAIL_ACTOR,
                          {"directUrls": urls, "resultsType": "posts"},
                          "tracked")
    except RuntimeError as err:
        DEBUG["tracked_error"] = str(err)[:300]
        return []
    reels = []
    for item in items:
        reel = extract_reel(item, now)
        if not reel:
            continue
        entry = meta.get(reel["shortcode"])
        if entry:
            reel["category"] = entry.get("category") or reel["category"]
            if reel["followers"] is None:
                reel["followers"] = entry.get("followers")
        reels.append(reel)
    return reels


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


def build_report(viral: list[dict], rising: list[dict],
                 fresh: list[dict], now: datetime) -> str:
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
        f"## 🌱 Новые кандидаты в трекинге — {len(fresh)} шт.",
        "",
        "Свежие ролики малых каналов, за которыми агент следит ежедневно —",
        "если начнут разгоняться, попадут в разделы выше с замеренной скоростью.",
        "",
    ]
    if fresh:
        lines += [
            "| # | Тема | Просмотры | Подписчики | ×аудитория | В час | Возраст | Автор | Ролик |",
            "|---|------|-----------|------------|------------|-------|---------|-------|-------|",
        ]
        for i, r in enumerate(fresh[:10], 1):
            lines.append(
                f"| {i} | {CATEGORY_LABELS[r['category']]} | {fmt_views(r['views'])} "
                f"| {fmt_followers(r)} | {fmt_multiplier(r)} "
                f"| {fmt_views(r['vph'])} | {fmt_hours(r['age_hours'])} "
                f"| @{r['author']} | [{snippet(r['caption'], 45) or r['shortcode']}]({r['url']}) |"
            )
    else:
        lines.append("_Пока пусто — кандидаты появятся по мере сбора._")

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


def _tg_entry(index: int, reel: dict, headline: str) -> str:
    """One reel as a compact HTML block for Telegram."""
    caption = html.escape(snippet(reel["caption"], 60) or reel["shortcode"])
    link = f'<a href="{html.escape(reel["url"], quote=True)}">{caption}</a>'
    author = html.escape(reel["author"])
    return (
        f"{index}. {headline} · {fmt_views(reel['views'])} 👁 "
        f"· <b>{fmt_multiplier(reel)}</b>\n"
        f"{CATEGORY_LABELS[reel['category']]} · @{author} "
        f"({fmt_followers(reel)} подписчиков)\n{link}"
    )


def build_telegram_messages(viral: list[dict], rising: list[dict],
                            fresh: list[dict], now: datetime) -> list[str]:
    """The full report as Telegram-sized HTML messages (no tables)."""
    blocks = [
        f"🎬 <b>Reels-отчёт {now.strftime('%d.%m.%Y')}</b>\n"
        f"Книги · успех · бизнес. Авторы до {fmt_views(MAX_FOLLOWERS)} "
        f"подписчиков, ролики моложе {MAX_AGE_DAYS} дн.\n"
        f"«×N» — во сколько раз просмотры больше аудитории.",
        f"🏆 <b>Пробили 1 млн — {len(viral)}</b> (быстрые сверху)",
    ]
    if viral:
        for i, r in enumerate(viral, 1):
            mark = "✅" if r["measured"] else "≈"
            blocks.append(
                _tg_entry(i, r, f"<b>{fmt_hours(r['ttm'])} до 1 млн</b> {mark}")
            )
    else:
        blocks.append("Сегодня таких нет.")

    blocks.append(f"🚀 <b>На подлёте к миллиону — {len(rising)}</b>")
    if rising:
        for i, r in enumerate(rising, 1):
            eta = (VIRAL_VIEWS - r["views"]) / max(r["vph"], 1)
            blocks.append(
                _tg_entry(i, r, f"прогноз ~{fmt_hours(r['age_hours'] + eta)}")
            )
    else:
        blocks.append("Кандидатов нет.")

    blocks.append(f"🌱 <b>Новые в трекинге — {len(fresh)}</b>")
    if fresh:
        for i, r in enumerate(fresh[:10], 1):
            blocks.append(_tg_entry(i, r, f"{fmt_views(r['vph'])}/час"))
    else:
        blocks.append("Пока пусто.")

    messages, current = [], ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > 3800 and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def send_telegram(viral: list[dict], rising: list[dict],
                  fresh: list[dict], now: datetime) -> None:
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
    tg_debug["chat_id"] = chat_id
    sent = 0
    for message in build_telegram_messages(viral, rising, fresh, now):
        try:
            _http_json(
                f"https://api.telegram.org/bot{token}/sendMessage",
                {"chat_id": chat_id, "text": message, "parse_mode": "HTML",
                 "disable_web_page_preview": True},
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001 — try again without HTML
            try:
                _http_json(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    {"chat_id": chat_id,
                     "text": re.sub(r"<[^>]+>", "", message),
                     "disable_web_page_preview": True},
                )
                sent += 1
                tg_debug["html_fallback"] = str(exc)[:200]
            except Exception as exc2:  # noqa: BLE001
                tg_debug["send_error"] = str(exc2)[:300]
                print(f"Telegram send failed: {exc2}", file=sys.stderr)
                break
    tg_debug["sent"] = sent > 0
    tg_debug["messages_sent"] = sent
    if sent:
        print(f"Telegram report sent in {sent} message(s)")


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
    history = load_history()
    posts = fetch_posts(token)

    reels_by_code: dict[str, dict] = {}
    for post in posts:
        reel = extract_reel(post, now)
        if reel:
            reels_by_code.setdefault(reel["shortcode"], reel)
    for reel in refetch_tracked(token, history, set(reels_by_code), now):
        reels_by_code.setdefault(reel["shortcode"], reel)
    reels = list(reels_by_code.values())
    enrich_followers(token, reels)

    # The whole point: keep only small accounts, where big view counts
    # mean the algorithm pushed the reel, not the author's own audience.
    kept = []
    for reel in reels:
        if reel["watchlist"] or (
            reel["followers"] is not None
            and reel["followers"] <= MAX_FOLLOWERS
        ):
            kept.append(reel)
        else:
            _skip("no_followers_data" if reel["followers"] is None
                  else "too_many_followers")
    reels = kept
    print(f"{len(reels)} fresh English small-account reels after filtering")

    update_history(history, reels, now)

    viral, rising, fresh = [], [], []
    for reel in reels:
        ttm = hours_to_million(reel, history)
        outperform = (
            reel["followers"] and reel["views"] >= 5 * reel["followers"]
        )
        if ttm is not None:
            reel["ttm"], reel["measured"] = ttm
            viral.append(reel)
        elif reel["views"] >= RISING_MIN_VIEWS and (
            reel["vph"] >= RISING_MIN_VPH or outperform
        ):
            rising.append(reel)
        else:
            fresh.append(reel)
    viral.sort(key=lambda r: r["ttm"])
    rising.sort(key=lambda r: r["vph"], reverse=True)
    fresh.sort(
        key=lambda r: r["views"] / max(r["followers"] or 10**9, 1),
        reverse=True,
    )

    report = build_report(viral, rising, fresh, now)
    REPORTS_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"{now.strftime('%Y-%m-%d')}.md"
    report_path.write_text(report)
    (REPORTS_DIR / "latest.md").write_text(report)
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1))
    print(f"Report written to {report_path}")

    send_telegram(viral, rising, fresh, now)

    DEBUG["reels_kept"] = len(reels)
    DEBUG["run_at"] = now.isoformat()
    (DATA_DIR / "debug_last_run.json").write_text(
        json.dumps(DEBUG, ensure_ascii=False, indent=1)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
