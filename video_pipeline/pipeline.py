"""End-to-end orchestrator: competitor analysis -> script -> storyboard ->
voiceover (ElevenLabs) -> scene images (Nano Banana / Gemini) -> music
(ElevenLabs) -> final render (ffmpeg).

Usage:
    python3 pipeline.py --niche "AI productivity tools" --title "5 AI Tools That Replaced My To-Do List"

Any stage without a configured/reachable API key degrades gracefully (silence,
placeholder frames, a soft tone) so the full pipeline always produces a valid
MP4 you can inspect, even before all keys are wired up.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import compose
import image_gen
import voiceover
from competitor_analysis import analyze_competitors
from music import generate_music
from script_writer import generate_script, generate_storyboard
from config import OUTPUT_DIR


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def run(
    niche: str,
    title: str | None,
    length_minutes: int,
    scene_seconds: float,
    orientation: str,
    voice_id: str,
) -> Path:
    title = title or f"What's Actually Working in {niche.title()} Right Now"
    run_dir = OUTPUT_DIR / slugify(title)
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/6] Analyzing competitors for niche: {niche!r}")
    try:
        report = analyze_competitors(niche)
        research = report.as_research_text()
    except Exception as exc:
        print(f"  competitor analysis unavailable ({exc}); continuing without it.")
        research = f"(No competitor data available. Niche: {niche})"
    (run_dir / "research.txt").write_text(research)
    print(research[:500])

    print("[2/6] Writing script")
    script = generate_script(niche, title, research, length_minutes)
    (run_dir / "script.txt").write_text(script)

    print("[3/6] Building storyboard")
    storyboard = generate_storyboard(title, script, scene_seconds)
    for i, scene in enumerate(storyboard.scenes):
        print(f"  scene {i}: {scene.narration[:60]!r}")

    print("[4/6] Generating voiceover")
    vo_path = voiceover.generate_voiceover(
        storyboard.full_narration(), run_dir / "voiceover.mp3", voice_id=voice_id,
        fallback_seconds=storyboard.total_duration(),
    )
    voice_duration = compose.get_duration(vo_path)
    print(f"  voiceover duration: {voice_duration:.1f}s")

    # Rescale scene durations proportionally to match the actual voiceover length.
    word_counts = [max(len(s.narration.split()), 1) for s in storyboard.scenes]
    total_words = sum(word_counts)
    scene_durations = [voice_duration * wc / total_words for wc in word_counts]

    print("[5/6] Generating scene images (Nano Banana) and background music")
    scene_images = [
        image_gen.generate_scene_image(
            scene.image_prompt, run_dir / f"scene_{i:03d}.png", orientation
        )
        for i, scene in enumerate(storyboard.scenes)
    ]
    music_path = generate_music(
        f"Background music for a video about {niche}, subtle, instrumental, unobtrusive",
        duration_seconds=voice_duration,
        out_path=run_dir / "music.mp3",
    )

    print("[6/6] Rendering final video")
    final_path = compose.render_final_video(
        scene_images=scene_images,
        scene_durations=scene_durations,
        voiceover_path=vo_path,
        music_path=music_path,
        out_path=run_dir / "final.mp4",
        orientation=orientation,
    )
    print(f"Done: {final_path}")
    return final_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--niche", required=True, help="Topic/niche to research and make a video about")
    parser.add_argument("--title", default=None, help="Video title (auto-generated if omitted)")
    parser.add_argument("--length-minutes", type=int, default=2)
    parser.add_argument("--scene-seconds", type=float, default=6.0)
    parser.add_argument("--orientation", choices=["landscape", "vertical"], default="landscape")
    parser.add_argument("--voice-id", default=voiceover.DEFAULT_VOICE_ID)
    args = parser.parse_args()

    run(
        niche=args.niche,
        title=args.title,
        length_minutes=args.length_minutes,
        scene_seconds=args.scene_seconds,
        orientation=args.orientation,
        voice_id=args.voice_id,
    )


if __name__ == "__main__":
    main()
