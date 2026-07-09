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
5. **Scene images** (`image_gen.py`) — generates one image per scene. Tries
   OpenAI (`gpt-image-1`, `OPENAI_API_KEY`) first if set, then falls back to
   Google's "Nano Banana" model (`gemini-2.5-flash-image`, same
   `GEMINI_API_KEY` as the script stage — note its free tier has a 0 quota
   for image generation; billing must be enabled), then a placeholder frame.
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
- `GEMINI_API_KEY` — script/storyboard text (and Nano Banana scene images, if billing is enabled)
- `OPENAI_API_KEY` — optional, scene images via `gpt-image-1` (tried before Gemini images)
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

Some sandboxed execution environments enforce an egress allowlist. On
Claude Code on the web, the environment's **Network access** setting
(Trusted/Custom/Full) controls this — see
[docs](https://code.claude.com/docs/en/claude-code-on-the-web#network-access).
The default **Trusted** level allows `*.googleapis.com` (so Gemini works
out of the box) but blocks `www.youtube.com`, `api.elevenlabs.io`, and
`api.openai.com`. To unblock them, edit the environment (cloud icon →
settings) and switch to **Custom** with those domains added — changes apply
to new sessions only, not the one you're editing from. Stage 1
(competitor analysis via `www.youtube.com`) still needs that domain added
separately if you want live results instead of the "no competitor data"
fallback.
