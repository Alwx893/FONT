"""Loads pipeline configuration from video_pipeline/.env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PACKAGE_DIR / "output"

load_dotenv(PACKAGE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    elevenlabs_api_key: str | None
    gemini_api_key: str | None
    youtube_api_key: str | None

    @property
    def has_elevenlabs(self) -> bool:
        return bool(self.elevenlabs_api_key)

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_youtube_api(self) -> bool:
        return bool(self.youtube_api_key)


def load_settings() -> Settings:
    return Settings(
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        youtube_api_key=os.getenv("YOUTUBE_API_KEY") or None,
    )
