"""CLI: render one two-speaker dialogue video from a raw script text file.

Usage:
    python3 render_dialogue_video.py --script video1.txt --title "5-ai-art-tax" \
        --music assets/music/track_01_rj_al_maghribi.mp4
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from dialogue_script import parse_dialogue
from dialogue_voiceover import generate_turn_audio
from dialogue_compose import render_dialogue_video
from config import OUTPUT_DIR


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def run(script_path: Path, title: str, music_path: Path) -> Path:
    run_dir = OUTPUT_DIR / "dialogues" / slugify(title)
    run_dir.mkdir(parents=True, exist_ok=True)

    raw_script = script_path.read_text(encoding="utf-8")
    turns = parse_dialogue(raw_script)
    print(f"[1/3] Parsed {len(turns)} dialogue turns")

    print("[2/3] Generating per-turn voiceover (ElevenLabs)")
    turn_audios = generate_turn_audio(turns, run_dir / "turns")

    print("[3/3] Rendering final video")
    final_path = render_dialogue_video(
        turn_audios, music_path, run_dir / "final.mp4", run_dir / "_tmp",
    )
    print(f"Done: {final_path}")
    return final_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--music", required=True, type=Path)
    args = parser.parse_args()
    run(args.script, args.title, args.music)


if __name__ == "__main__":
    main()
