"""Per-turn ElevenLabs voiceover generation for two-speaker dialogue scripts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dialogue_script import Turn
from voiceover import generate_voiceover
from compose import get_duration

VOICE_IDS = {
    "hoodie": "MVfoIaeG0ULeEdBWhEMq",
    "briefcase": "GhnhBVjKugEQprWDXqwj",
}


@dataclass
class TurnAudio:
    turn: Turn
    audio_path: Path
    duration: float


def generate_turn_audio(turns: list[Turn], out_dir: Path) -> list[TurnAudio]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for turn in turns:
        voice_id = VOICE_IDS[turn.speaker]
        out_path = out_dir / f"turn_{turn.index:03d}_{turn.speaker}.mp3"
        if out_path.exists() and out_path.stat().st_size > 0:
            path = out_path
        else:
            path = generate_voiceover(
                turn.text, out_path, voice_id=voice_id,
                fallback_seconds=max(len(turn.text.split()) / 2.5, 1.0),
            )
        duration = get_duration(path)
        results.append(TurnAudio(turn=turn, audio_path=path, duration=duration))
        print(f"  [{turn.index:02d}] {turn.speaker:>9} ({duration:4.1f}s): {turn.text[:60]}")
    return results
