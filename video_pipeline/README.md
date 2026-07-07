# video_pipeline

Automated video generation: competitor research -> script -> storyboard ->
voiceover -> AI scene images -> music -> final render.

## Pipeline stages

1. **Competitor analysis** (`competitor_analysis.py`) — searches YouTube via
   `yt-dlp` (no API key required) for the given niche and pulls the current
   top videos (title, channel, views) as research input. vidIQ is not used
   here (temporarily disabled — no credits); swap this module for vidIQ's
   MCP tools later if you want richer breakout/outlier data.
2. **Script** (`script_writer.py`) — writes a spoken video script grounded in
   the competitor research, using Gemini (`GEMINI_API_KEY`).
3. **Storyboard** (`script_writer.py`) — splits the script into timed scenes,
   each with a narration slice and an image-generation prompt.
4. **Voiceover** (`voiceover.py`) — synthesizes the narration with ElevenLabs
   TTS (`ELEVENLABS_API_KEY`).
5. **Scene images** (`image_gen.py`) — generates one image per scene with
   Google's "Nano Banana" model (`gemini-2.5-flash-image`, same
   `GEMINI_API_KEY`).
6. **Music** (`music.py`) — generates a background track with ElevenLabs
   Music (same `ELEVENLABS_API_KEY`).
7. **Render** (`compose.py`) — turns each scene image into a Ken Burns
   pan/zoom clip with `ffmpeg`, concatenates them, mixes voiceover + music,
   and muxes the final MP4.

Every stage that calls an external API degrades gracefully if the key is
missing or the call fails (silence, a placeholder frame, a soft tone), so
`pipeline.py` always produces a valid MP4 end to end — useful for checking
timing/wiring before all keys are live.

## Setup

```bash
cd video_pipeline
python3 -m venv ../.venv && source ../.venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your real keys
```

Requires `ffmpeg` on PATH.

Keys needed for real (non-fallback) output:
- `ELEVENLABS_API_KEY` — voiceover + music
- `GEMINI_API_KEY` — script/storyboard text + Nano Banana scene images
- `YOUTUBE_API_KEY` — optional, not currently used (yt-dlp needs no key)

`.env` is gitignored — never commit real keys. If a key was ever pasted into
a chat/log, rotate it in that provider's dashboard.

## Run

```bash
python3 pipeline.py --niche "AI productivity tools" \
  --title "5 AI Tools That Replaced My To-Do List" \
  --length-minutes 2 --scene-seconds 6 --orientation landscape
```

Output lands in `output/<slugified-title>/`: `research.txt`, `script.txt`,
per-scene PNGs, `voiceover.*`, `music.*`, and `final.mp4`.

## Known environment limitation (sandboxed sessions)

Some sandboxed execution environments (including the one this pipeline was
built and smoke-tested in) enforce an egress allowlist that blocks
`www.youtube.com` and `api.elevenlabs.io`. In that case stage 1 and stages
4/6 automatically fall back (see above) — the mechanics are verified, but
the actual voice/music/competitor-data content is not real. Run this outside
such a sandbox (a normal machine/CI runner) with real keys to get real
output. `generativelanguage.googleapis.com` (Gemini/Nano Banana) was
reachable in that same sandbox, for reference.
