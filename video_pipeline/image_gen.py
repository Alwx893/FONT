"""Scene image generation via Google's "Nano Banana" (gemini-2.5-flash-image).

Falls back to a locally-rendered placeholder frame (solid gradient + the
prompt text) when GEMINI_API_KEY is missing or the call fails, so the compose
stage can still be exercised end to end without a live key.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from config import load_settings

IMAGE_MODEL = "gemini-2.5-flash-image"


def _placeholder_image(prompt: str, out_path: Path, size: tuple[int, int]) -> Path:
    from PIL import Image, ImageDraw

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color=(20, 22, 28))
    draw = ImageDraw.Draw(img)
    wrapped = textwrap.fill(prompt, width=28)
    draw.multiline_text(
        (size[0] / 2, size[1] / 2), wrapped, fill=(230, 230, 230),
        anchor="mm", align="center", spacing=10,
    )
    img.save(out_path)
    return out_path


def generate_scene_image(
    prompt: str,
    out_path: Path,
    orientation: str = "landscape",
) -> Path:
    size = (1920, 1080) if orientation == "landscape" else (1080, 1920)
    settings = load_settings()
    if not settings.has_gemini:
        return _placeholder_image(prompt, out_path, size)

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        resp = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=[f"{prompt}. No text, no watermarks, no logos in the image."],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        for part in resp.candidates[0].content.parts:
            if part.inline_data:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(part.inline_data.data)
                return out_path
        raise RuntimeError("no inline image data in Gemini response")
    except Exception as exc:
        print(f"[image_gen] Nano Banana generation failed ({exc}); using placeholder frame.")
        return _placeholder_image(prompt, out_path, size)


if __name__ == "__main__":
    import sys

    prompt = " ".join(sys.argv[1:]) or "A cluttered desk transforming into an organized digital workspace, cinematic lighting"
    path = generate_scene_image(prompt, Path("output/test_scene.png"))
    print(f"Wrote {path}")
