"""Assembles a two-speaker dialogue video: looped character footage per turn,
burned-in word-by-word captions (Courier New), voiceover per turn, and
background music underneath."""
from __future__ import annotations

import subprocess
from pathlib import Path

from dialogue_voiceover import TurnAudio
from compose import get_duration, mix_audio, mux_video_audio, concat_clips

ASSETS = Path(__file__).resolve().parent / "assets"
FOOTAGE = ASSETS / "footage"
CAPTION_FONT = ASSETS / "fonts" / "CourierNew.ttf"

CANVAS = (1080, 1920)
FPS = 30
CAPTION_FONTSIZE = 88  # "size 10" doesn't map to a literal px value; tell me to resize
CAPTION_Y = 480  # above the characters' heads
CAPTION_MIN_WORD_SECONDS = 0.14

FOOTAGE_BY_SPEAKER = {
    "hoodie": FOOTAGE / "hoodie_solo.mp4",
    "briefcase": FOOTAGE / "briefcase_solo.mp4",
}
ESTABLISHING_SHOT = FOOTAGE / "both_establishing.mp4"


def _escape_ffmpeg_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")


def _word_timings(text: str, duration: float) -> list[tuple[str, float, float]]:
    """Evenly-paced (by character count) word-by-word timing within `duration`.

    Approximate: we don't have per-word timestamps from ElevenLabs, so this
    distributes time proportionally to word length with a floor per word.
    """
    words = text.split()
    if not words:
        return []
    weights = [max(len(w), 1) for w in words]
    total_weight = sum(weights)
    raw = [duration * w / total_weight for w in weights]
    raw = [max(s, CAPTION_MIN_WORD_SECONDS) for s in raw]
    scale = duration / sum(raw)
    timings = []
    t = 0.0
    for word, seg in zip(words, raw):
        seg *= scale
        timings.append((word, t, t + seg))
        t += seg
    return timings


def _build_caption_filter(turn_audio: TurnAudio, workdir: Path) -> str:
    turn = turn_audio.turn
    words_dir = workdir / "words"
    words_dir.mkdir(parents=True, exist_ok=True)
    timings = _word_timings(turn.text, max(turn_audio.duration, 0.3))

    parts = []
    for i, (word, start, end) in enumerate(timings):
        word_file = words_dir / f"w_{turn.index:03d}_{i:03d}.txt"
        word_file.write_text(word, encoding="utf-8")
        parts.append(
            f"drawtext=fontfile='{_escape_ffmpeg_path(CAPTION_FONT)}':"
            f"textfile='{_escape_ffmpeg_path(word_file)}':"
            f"fontcolor=white:fontsize={CAPTION_FONTSIZE}:"
            "box=0:borderw=3:bordercolor=black@0.85:"
            f"x=(w-text_w)/2:y={CAPTION_Y}:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
    return ",".join(parts)


def render_turn_clip(
    turn_audio: TurnAudio,
    out_path: Path,
    workdir: Path,
    is_first_turn: bool,
) -> Path:
    turn = turn_audio.turn
    footage = ESTABLISHING_SHOT if is_first_turn else FOOTAGE_BY_SPEAKER[turn.speaker]
    duration = max(turn_audio.duration, 0.3)

    base_filters = [
        f"scale={CANVAS[0]}:{CANVAS[1]}:force_original_aspect_ratio=increase",
        f"crop={CANVAS[0]}:{CANVAS[1]}",
    ]
    caption_filter = _build_caption_filter(turn_audio, workdir)

    cmd = [
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(footage),
        "-vf", ",".join(base_filters + [caption_filter]),
        "-t", str(duration), "-r", str(FPS), "-an",
        str(out_path),
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def concat_turn_audio(turn_audios: list[TurnAudio], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = out_path.with_suffix(".txt")
    list_file.write_text(
        "\n".join(f"file '{t.audio_path.resolve()}'" for t in turn_audios)
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


def render_dialogue_video(
    turn_audios: list[TurnAudio],
    music_path: Path,
    out_path: Path,
    workdir: Path,
) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)

    clips = [
        render_turn_clip(
            ta, workdir / f"clip_{ta.turn.index:03d}.mp4", workdir,
            is_first_turn=(ta.turn.index == 0),
        )
        for ta in turn_audios
    ]
    silent_video = concat_clips(clips, workdir / "silent.mp4")
    full_voice = concat_turn_audio(turn_audios, workdir / "voice_full.mp3")
    mixed_audio = mix_audio(full_voice, music_path, workdir / "mixed_audio.m4a", music_volume=0.12)
    return mux_video_audio(silent_video, mixed_audio, out_path)
