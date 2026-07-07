"""Background music via ElevenLabs Music.

Falls back to a soft synthesized pad tone (ffmpeg sine synth) when
ELEVENLABS_API_KEY is missing or the call fails, so the compose stage can
still be exercised end to end without a live key.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from config import load_settings


def _fallback_tone(out_path: Path, duration_seconds: float) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=220:duration={max(duration_seconds, 1)}",
            "-af", "volume=0.05",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


def generate_music(prompt: str, duration_seconds: float, out_path: Path) -> Path:
    settings = load_settings()
    if not settings.has_elevenlabs:
        return _fallback_tone(out_path.with_suffix(".wav"), duration_seconds)

    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    try:
        audio = client.music.compose(
            prompt=prompt,
            music_length_ms=int(duration_seconds * 1000),
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            for chunk in audio:
                f.write(chunk)
        return out_path
    except Exception as exc:
        print(f"[music] ElevenLabs Music failed ({exc}); using fallback tone.")
        return _fallback_tone(out_path.with_suffix(".wav"), duration_seconds)


if __name__ == "__main__":
    path = generate_music(
        "Upbeat lofi instrumental background music, gentle beat, no vocals",
        duration_seconds=20,
        out_path=Path("output/test_music.mp3"),
    )
    print(f"Wrote {path}")
