"""Parses two-speaker dialogue scripts (em-dash prefixed lines) into turns.

Format expected (Russian dialogue style used for these videos):
    — Line one (asker speaks first)
    — Line two (explainer replies)
    — Line three (asker)
    ...

Turns alternate strictly starting with the asker (hoodie character).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ASKER = "hoodie"
EXPLAINER = "briefcase"

DASH_PREFIX = re.compile(r"^[—\-–]\s*")


@dataclass
class Turn:
    index: int
    speaker: str  # "hoodie" or "briefcase"
    text: str


def parse_dialogue(raw_script: str) -> list[Turn]:
    lines = [ln.strip() for ln in raw_script.splitlines() if ln.strip()]
    turns: list[Turn] = []
    for i, line in enumerate(lines):
        text = DASH_PREFIX.sub("", line).strip()
        if not text:
            continue
        speaker = ASKER if len(turns) % 2 == 0 else EXPLAINER
        turns.append(Turn(index=len(turns), speaker=speaker, text=text))
    return turns


if __name__ == "__main__":
    import sys

    sample = sys.stdin.read()
    for t in parse_dialogue(sample):
        print(f"[{t.index:02d}] {t.speaker:>9}: {t.text}")
