"""ElevenLabs text-to-speech for scene narration.

Falls back to generating silence (via ffmpeg) when ELEVENLABS_API_KEY is
missing or the API call fails, so the rest of the pipeline (timing, mixing,
render) can still be exercised end to end without a live key/network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from config import load_settings

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs "Rachel" — default sample voice


def list_voices() -> list[dict]:
    settings = load_settings()
    if not settings.has_elevenlabs:
        return []
    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    resp = client.voices.get_all()
    return [{"voice_id": v.voice_id, "name": v.name} for v in resp.voices]


def _write_silence(out_path: Path, seconds: float) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=mono",
            "-t", str(max(seconds, 0.5)),
            "-q:a", "9", str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


def generate_voiceover(
    text: str,
    out_path: Path,
    voice_id: str = DEFAULT_VOICE_ID,
    fallback_seconds: float = 5.0,
) -> Path:
    """Synthesize `text` to `out_path` (mp3). Falls back to silence on failure."""
    settings = load_settings()
    if not settings.has_elevenlabs:
        return _write_silence(out_path.with_suffix(".wav"), fallback_seconds)

    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    try:
        audio = client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id="eleven_v3",
            output_format="mp3_44100_128",
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            for chunk in audio:
                f.write(chunk)
        return out_path
    except Exception as exc:  # network/policy/quota failures — degrade gracefully
        print(f"[voiceover] ElevenLabs TTS failed ({exc}); using silence fallback.")
        return _write_silence(out_path.with_suffix(".wav"), fallback_seconds)


if __name__ == "__main__":
    import sys

    text = " ".join(sys.argv[1:]) or "This is a test of the ElevenLabs voiceover pipeline."
    path = generate_voiceover(text, Path("output/test_voiceover.mp3"))
    print(f"Wrote {path}")
