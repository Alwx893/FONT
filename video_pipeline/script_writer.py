"""Script + storyboard generation, grounded in competitor research.

Uses Gemini (the same GEMINI_API_KEY needed for Nano Banana image generation,
so the pipeline needs only two provider keys total: Google + ElevenLabs).
Falls back to a deterministic template when no key is configured, so the rest
of the pipeline can still run end to end without a live key.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from config import load_settings

TEXT_MODEL = "gemini-2.5-flash"


@dataclass
class Scene:
    narration: str
    image_prompt: str
    duration: float = 6.0


@dataclass
class Storyboard:
    title: str
    scenes: list[Scene] = field(default_factory=list)

    def full_narration(self) -> str:
        return " ".join(s.narration for s in self.scenes)

    def total_duration(self) -> float:
        return sum(s.duration for s in self.scenes)


def _gemini_text(prompt: str) -> str | None:
    settings = load_settings()
    if not settings.has_gemini:
        return None
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        resp = client.models.generate_content(model=TEXT_MODEL, contents=prompt)
        return resp.text
    except Exception as exc:
        print(f"[script_writer] Gemini text generation failed ({exc}); using fallback template.")
        return None


def generate_script(niche: str, title: str, research: str, length_minutes: int = 3) -> str:
    prompt = f"""You are a YouTube scriptwriter. Write a spoken video script (~{length_minutes} minute(s) at ~150 words/minute).

Niche: {niche}
Title: {title}

Ground the script in this competitor research (what's already working in this niche,
so the new video can differentiate and cover gaps):
{research}

Write ONLY the spoken narration text, no scene directions, no timestamps, no headers.
Hook in the first two sentences. End with a call to action."""

    text = _gemini_text(prompt)
    if text:
        return text.strip()

    return (
        f"Welcome back! Today we're diving into {niche}. "
        f"Everyone's talking about it right now, but most videos miss the real story. "
        f"Here's what you actually need to know about {title.lower()}. "
        f"Stick around, because the last point changes everything. "
        f"If this helped, subscribe for more deep dives like this one."
    )


def _parse_scene_json(raw: str) -> list[dict] | None:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def generate_storyboard(title: str, script: str, scene_seconds: float = 6.0) -> Storyboard:
    prompt = f"""Break this video script into short scenes for an AI-generated visual sequence.

Script:
{script}

Return ONLY a JSON array (no prose, no markdown fences). Each element:
{{"narration": "<the slice of narration for this scene, verbatim from the script>",
  "image_prompt": "<a vivid, concrete visual description for an AI image generator, no text/words in the image>"}}

Split narration into natural {scene_seconds:.0f}-second chunks (~{int(scene_seconds * 2.5)} words each)."""

    raw = _gemini_text(prompt)
    parsed = _parse_scene_json(raw) if raw else None

    if parsed:
        scenes = [
            Scene(
                narration=item.get("narration", "").strip(),
                image_prompt=item.get("image_prompt", "").strip(),
                duration=scene_seconds,
            )
            for item in parsed
            if item.get("narration")
        ]
        if scenes:
            return Storyboard(title=title, scenes=scenes)

    # Fallback: naive sentence-chunking, generic image prompts.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]
    scenes = [
        Scene(
            narration=sentence,
            image_prompt=f"Cinematic, high-detail illustration representing: {sentence}",
            duration=scene_seconds,
        )
        for sentence in sentences
    ]
    return Storyboard(title=title, scenes=scenes)


if __name__ == "__main__":
    script = generate_script(
        niche="AI productivity tools",
        title="5 AI Tools That Replaced My To-Do List",
        research="Competitor videos focus on listicles of tools with screen recordings.",
        length_minutes=1,
    )
    print("--- SCRIPT ---")
    print(script)
    board = generate_storyboard("5 AI Tools That Replaced My To-Do List", script)
    print("--- STORYBOARD ---")
    for i, scene in enumerate(board.scenes, 1):
        print(f"{i}. [{scene.duration}s] {scene.narration}")
        print(f"   image: {scene.image_prompt}")
