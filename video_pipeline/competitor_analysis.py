"""Competitor analysis without vidIQ.

Uses yt-dlp (no API key needed) to search YouTube for a niche/keyword and pull
back the current top-performing videos: title, channel, view count, and
description. This stands in for "vidIQ analyzes competitors" — it's the
research input fed into the script generator.

If YOUTUBE_API_KEY is set, prefer it in the future for richer data (tags,
category, exact publish cadence) — not implemented here since yt-dlp already
covers what the script writer needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yt_dlp


@dataclass
class CompetitorVideo:
    title: str
    channel: str
    view_count: int | None
    url: str
    description: str = ""

    def as_research_line(self) -> str:
        views = f"{self.view_count:,}" if self.view_count else "?"
        return f"- \"{self.title}\" by {self.channel} ({views} views) — {self.url}"


@dataclass
class CompetitorReport:
    niche: str
    videos: list[CompetitorVideo] = field(default_factory=list)

    def top(self, n: int = 10) -> list[CompetitorVideo]:
        return sorted(
            self.videos, key=lambda v: v.view_count or 0, reverse=True
        )[:n]

    def as_research_text(self, n: int = 10) -> str:
        lines = [f"Top performing competitor videos for niche '{self.niche}':"]
        lines.extend(v.as_research_line() for v in self.top(n))
        return "\n".join(lines)


def analyze_competitors(niche: str, limit: int = 15) -> CompetitorReport:
    """Search YouTube for `niche` and return the top videos by view count."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    search_query = f"ytsearch{limit}:{niche}"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(search_query, download=False)

    entries = result.get("entries", []) if result else []
    videos = [
        CompetitorVideo(
            title=entry.get("title") or "",
            channel=entry.get("channel") or entry.get("uploader") or "",
            view_count=entry.get("view_count"),
            url=entry.get("url") or entry.get("webpage_url") or "",
            description=(entry.get("description") or "")[:500],
        )
        for entry in entries
        if entry
    ]
    return CompetitorReport(niche=niche, videos=videos)


if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "AI productivity tools"
    report = analyze_competitors(query)
    print(report.as_research_text())
