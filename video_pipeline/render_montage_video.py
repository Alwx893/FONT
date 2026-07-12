"""CLI: render a single-narrator 'pattern' montage video (product cutaways,
AI-generated images) from a raw script with inline (visual direction) cues.

Usage:
    python3 render_montage_video.py --script script.txt --title "loss-leaders"
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import compose
import image_gen
import voiceover
from montage_script import MontageLine, VisualBeat, parse_montage_script
from music import generate_music
from config import OUTPUT_DIR
from dialogue_compose import CAPTION_FONT, CAPTION_FONTSIZE, CAPTION_Y, CAPTION_MIN_WORD_SECONDS, _escape_ffmpeg_path

NARRATOR_VOICE_ID = "GhnhBVjKugEQprWDXqwj"  # "briefcase" voice, used as sole narrator here
ORIENTATION = "vertical"
CTA_TEXT = "Забери суть лучших книг в КРАТКО. Ссылка в профиле."
CTA_IMAGE_PROMPT = "крупный товарный план: стопка книг рядом со смартфоном на экране которого открыто приложение для чтения саммари, студийный свет, нейтральный фон, фотореализм"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _word_timings(text: str, duration: float) -> list[tuple[str, float, float]]:
    words = text.split()
    if not words:
        return []
    weights = [max(len(w), 1) for w in words]
    total = sum(weights)
    raw = [max(duration * w / total, CAPTION_MIN_WORD_SECONDS) for w in weights]
    scale = duration / sum(raw)
    out, t = [], 0.0
    for w, seg in zip(words, raw):
        seg *= scale
        out.append((w, t, t + seg))
        t += seg
    return out


def _caption_filter(text: str, duration: float, workdir: Path, tag: str) -> str:
    words_dir = workdir / "words"
    words_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, (word, start, end) in enumerate(_word_timings(text, duration)):
        wf = words_dir / f"w_{tag}_{i:03d}.txt"
        wf.write_text(word, encoding="utf-8")
        parts.append(
            f"drawtext=fontfile='{_escape_ffmpeg_path(CAPTION_FONT)}':"
            f"textfile='{_escape_ffmpeg_path(wf)}':"
            f"fontcolor=white:fontsize={CAPTION_FONTSIZE}:"
            "box=0:borderw=3:bordercolor=black@0.85:"
            f"x=(w-text_w)/2:y={CAPTION_Y}:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
    return ",".join(parts) if parts else None


def render_line(line: MontageLine, run_dir: Path, workdir: Path, index: int) -> tuple[Path, Path]:
    """Returns (silent_video_clip, voice_audio) for one narrator line."""
    voice_path = voiceover.generate_voiceover(
        line.spoken_text, run_dir / "voice" / f"line_{index:03d}.mp3",
        voice_id=NARRATOR_VOICE_ID,
        fallback_seconds=max(len(line.spoken_text.split()) / 2.5, 1.0),
    )
    duration = compose.get_duration(voice_path)

    beats = line.beats or [VisualBeat(prompt="абстрактный фон, деньги, финансы", start_frac=0.0, end_frac=1.0)]
    beat_clips = []
    for bi, beat in enumerate(beats):
        beat_duration = max(duration * (beat.end_frac - beat.start_frac), 0.3)
        img = image_gen.generate_scene_image(
            beat.prompt, run_dir / "images" / f"line_{index:03d}_beat_{bi}.png", ORIENTATION,
        )
        clip = compose.make_scene_clip(
            img, beat_duration, workdir / f"beat_{index:03d}_{bi}.mp4", ORIENTATION,
        )
        beat_clips.append(clip)

    silent_line = compose.concat_clips(beat_clips, workdir / f"line_silent_{index:03d}.mp4") \
        if len(beat_clips) > 1 else beat_clips[0]

    caption_filter = _caption_filter(line.spoken_text, duration, workdir, f"{index:03d}")
    captioned = workdir / f"line_captioned_{index:03d}.mp4"
    if caption_filter:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(silent_line), "-vf", caption_filter, "-an", str(captioned)],
            check=True, capture_output=True,
        )
    else:
        captioned = silent_line

    return captioned, voice_path


def run(script_path: Path, title: str, music_prompt: str | None) -> Path:
    run_dir = OUTPUT_DIR / "montages" / slugify(title)
    workdir = run_dir / "_tmp"
    run_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    raw = script_path.read_text(encoding="utf-8")
    lines = parse_montage_script(raw)
    lines.append(MontageLine(
        index=len(lines), spoken_text=CTA_TEXT,
        beats=[VisualBeat(prompt=CTA_IMAGE_PROMPT, start_frac=0.0, end_frac=1.0)],
    ))
    print(f"[1/4] Parsed {len(lines)} narrator lines")

    video_clips, audio_clips = [], []
    for i, line in enumerate(lines):
        print(f"[2/4] Line {i}: {line.spoken_text[:60]}")
        v, a = render_line(line, run_dir, workdir, i)
        video_clips.append(v)
        audio_clips.append(a)

    print("[3/4] Concatenating and mixing")
    silent_full = compose.concat_clips(video_clips, workdir / "silent_full.mp4")

    voice_list = workdir / "voice_list.txt"
    voice_list.write_text("\n".join(f"file '{p.resolve()}'" for p in audio_clips))
    voice_full = workdir / "voice_full.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(voice_list), "-c", "copy", str(voice_full)],
        check=True, capture_output=True,
    )

    music_path = generate_music(
        music_prompt or "subtle corporate background beat, instrumental, unobtrusive",
        duration_seconds=compose.get_duration(voice_full),
        out_path=run_dir / "music.mp3",
    )
    mixed_audio = compose.mix_audio(voice_full, music_path, workdir / "mixed_audio.m4a", music_volume=0.12)

    print("[4/4] Final render")
    final_path = compose.mux_video_audio(silent_full, mixed_audio, run_dir / "final.mp4")
    print(f"Done: {final_path}")
    return final_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--music-prompt", default=None)
    args = parser.parse_args()
    run(args.script, args.title, args.music_prompt)


if __name__ == "__main__":
    main()
