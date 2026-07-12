"""Parses single-narrator 'loss-leader pattern' scripts with inline visual
directions, e.g.:

    «Xbox теряет деньги на этом (показывает на игровую приставку),
    чтобы заработать на этом (показывает диск с игрой).»

Each line becomes one TTS turn, split into N visual beats (one per
parenthetical direction) whose on-screen timing is approximated by the
character position of each direction within the spoken text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PAREN_RE = re.compile(r"\(([^)]+)\)")
SHOW_PREFIX_RE = re.compile(r"^показывает(?:\s+на)?\s+", re.IGNORECASE)
LEADING_QUOTE_RE = re.compile(r"^[«\"]\s*")
TRAILING_QUOTE_RE = re.compile(r"[»\"]")


@dataclass
class VisualBeat:
    prompt: str
    start_frac: float
    end_frac: float


@dataclass
class MontageLine:
    index: int
    spoken_text: str
    beats: list[VisualBeat] = field(default_factory=list)


def _to_image_prompt(direction: str) -> str:
    obj = SHOW_PREFIX_RE.sub("", direction).strip()
    return f"крупный товарный план: {obj}, студийный свет, нейтральный фон, фотореализм"


def parse_montage_script(raw: str) -> list[MontageLine]:
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    result = []
    for idx, line in enumerate(lines):
        clean = TRAILING_QUOTE_RE.sub("", LEADING_QUOTE_RE.sub("", line)).strip()

        matches = list(PAREN_RE.finditer(clean))
        if not matches:
            result.append(MontageLine(index=idx, spoken_text=clean, beats=[]))
            continue

        spoken_parts = []
        cursor = 0
        marker_positions = []  # char offset in spoken text where each paren occurred
        for m in matches:
            spoken_parts.append(clean[cursor:m.start()])
            marker_positions.append(sum(len(p) for p in spoken_parts))
            cursor = m.end()
        spoken_parts.append(clean[cursor:])
        spoken_text = "".join(spoken_parts)
        spoken_text = re.sub(r"\s+([,.!?])", r"\1", spoken_text)  # tidy stray spaces before punctuation
        spoken_text = re.sub(r"\s{2,}", " ", spoken_text).strip()

        total_chars = max(len(spoken_text), 1)
        beats = []
        bounds = [0] + [min(p, total_chars) for p in marker_positions] + [total_chars]
        for i, m in enumerate(matches):
            start_frac = bounds[i] / total_chars
            end_frac = bounds[i + 2] / total_chars if i + 2 < len(bounds) else 1.0
            beats.append(VisualBeat(
                prompt=_to_image_prompt(m.group(1)),
                start_frac=start_frac,
                end_frac=end_frac,
            ))
        # Ensure full coverage 0..1 with no gaps
        beats[0].start_frac = 0.0
        beats[-1].end_frac = 1.0

        result.append(MontageLine(index=idx, spoken_text=spoken_text, beats=beats))
    return result


if __name__ == "__main__":
    import sys

    for line in parse_montage_script(sys.stdin.read()):
        print(f"[{line.index}] {line.spoken_text}")
        for b in line.beats:
            print(f"    {b.start_frac:.2f}-{b.end_frac:.2f}: {b.prompt}")
