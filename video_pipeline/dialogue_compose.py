"""Assembles a two-speaker dialogue video: looped character footage per turn,
burned-in captions (Steelfish font, Cyrillic-safe), topical emoji icons,
voiceover per turn, and background music underneath."""
from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

from dialogue_voiceover import TurnAudio
from compose import get_duration, mix_audio, mux_video_audio, concat_clips

ASSETS = Path(__file__).resolve().parent / "assets"
FOOTAGE = ASSETS / "footage"
FONT_PATH = Path(__file__).resolve().parent.parent / "steelfish.bold.ttf"
EMOJI_FONT = Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf")

CANVAS = (1080, 1920)
FPS = 30

FOOTAGE_BY_SPEAKER = {
    "hoodie": FOOTAGE / "hoodie_solo.mp4",
    "briefcase": FOOTAGE / "briefcase_solo.mp4",
}
ESTABLISHING_SHOT = FOOTAGE / "both_establishing.mp4"

# Topical keyword -> emoji, checked in order; first match wins per turn.
EMOJI_KEYWORDS: list[tuple[str, str]] = [
    ("картин", "🎨"), ("музе", "🏛️"),
    ("паспорт", "🛂"), ("виз", "🛂"), ("иммунитет", "🛂"),
    ("самолет", "✈️"), ("самолёт", "✈️"), ("джет", "✈️"), ("таможн", "🛃"),
    ("час", "⌚"), ("ролекс", "⌚"), ("rolex", "⌚"),
    ("банк", "🏦"), ("кредит", "💳"),
    ("дубай", "🏙️"), ("виза", "🛂"),
    ("налог", "💰"), ("миллион", "💵"), ("доллар", "💵"), ("$", "💵"),
    ("компани", "🏢"), ("llc", "🏢"),
]


def pick_emoji(text: str) -> str | None:
    lowered = text.lower()
    for keyword, emoji in EMOJI_KEYWORDS:
        if keyword in lowered:
            return emoji
    return None


EMOJI_NATIVE_SIZE = 109  # NotoColorEmoji is a fixed-size bitmap-strike font


def _render_emoji_png(emoji: str, out_path: Path, size: int = 220) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    native = Image.new("RGBA", (EMOJI_NATIVE_SIZE, EMOJI_NATIVE_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(native)
    font = ImageFont.truetype(str(EMOJI_FONT), EMOJI_NATIVE_SIZE)
    draw.text((0, 0), emoji, font=font, embedded_color=True)
    img = native.resize((size, size), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def _escape_ffmpeg_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")


def render_turn_clip(
    turn_audio: TurnAudio,
    out_path: Path,
    workdir: Path,
    is_first_turn: bool,
) -> Path:
    turn = turn_audio.turn
    footage = ESTABLISHING_SHOT if is_first_turn else FOOTAGE_BY_SPEAKER[turn.speaker]
    duration = max(turn_audio.duration, 0.3)

    wrapped = textwrap.fill(turn.text, width=22)
    caption_file = workdir / f"caption_{turn.index:03d}.txt"
    caption_file.write_text(wrapped, encoding="utf-8")

    filters = [
        f"scale={CANVAS[0]}:{CANVAS[1]}:force_original_aspect_ratio=increase",
        f"crop={CANVAS[0]}:{CANVAS[1]}",
        (
            f"drawtext=fontfile='{_escape_ffmpeg_path(FONT_PATH)}':"
            f"textfile='{_escape_ffmpeg_path(caption_file)}':"
            "fontcolor=white:fontsize=64:line_spacing=14:"
            "box=1:boxcolor=black@0.55:boxborderw=24:"
            "x=(w-text_w)/2:y=h-560"
        ),
    ]

    emoji = pick_emoji(turn.text)
    inputs = ["-stream_loop", "-1", "-i", str(footage)]
    if emoji:
        emoji_png = workdir / f"emoji_{turn.index:03d}.png"
        _render_emoji_png(emoji, emoji_png)
        inputs += ["-i", str(emoji_png)]
        vf_main = ",".join(filters)
        filter_complex = (
            f"[0:v]{vf_main}[base];"
            f"[1:v]scale=160:160[icon];"
            f"[base][icon]overlay=W-200:120"
        )
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", filter_complex,
            "-t", str(duration), "-r", str(FPS), "-an",
            str(out_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-vf", ",".join(filters),
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
