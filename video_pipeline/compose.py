"""Final assembly: per-scene Ken Burns image clips + voiceover + music -> MP4."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

RESOLUTIONS = {"landscape": (1920, 1080), "vertical": (1080, 1920)}
FPS = 30


def get_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def make_scene_clip(
    image_path: Path,
    duration: float,
    out_path: Path,
    orientation: str = "landscape",
    zoom_per_frame: float = 0.0015,
) -> Path:
    w, h = RESOLUTIONS[orientation]
    frames = max(int(duration * FPS), 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={w*2}:{h*2},"
        f"zoompan=z='min(zoom+{zoom_per_frame},1.15)':d={frames}:s={w}x{h}:fps={FPS},"
        f"format=yuv420p"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
            "-vf", vf, "-t", str(duration), "-r", str(FPS),
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


def concat_clips(clip_paths: list[Path], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = out_path.with_suffix(".txt")
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths))
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


def mix_audio(
    voiceover_path: Path,
    music_path: Path,
    out_path: Path,
    music_volume: float = 0.15,
) -> Path:
    voice_duration = get_duration(voiceover_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{voice_duration},"
        f"volume={music_volume}[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(voiceover_path), "-i", str(music_path),
            "-filter_complex", filter_complex,
            "-map", "[aout]", str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


def mux_video_audio(video_path: Path, audio_path: Path, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(video_path), "-i", str(audio_path),
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            "-movflags", "+faststart",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


def render_final_video(
    scene_images: list[Path],
    scene_durations: list[float],
    voiceover_path: Path,
    music_path: Path,
    out_path: Path,
    orientation: str = "landscape",
    workdir: Path | None = None,
) -> Path:
    workdir = workdir or out_path.parent / "_tmp"
    workdir.mkdir(parents=True, exist_ok=True)

    clips = [
        make_scene_clip(img, dur, workdir / f"scene_{i:03d}.mp4", orientation)
        for i, (img, dur) in enumerate(zip(scene_images, scene_durations))
    ]
    silent_video = concat_clips(clips, workdir / "silent.mp4")
    mixed_audio = mix_audio(voiceover_path, music_path, workdir / "mixed_audio.m4a")
    return mux_video_audio(silent_video, mixed_audio, out_path)
