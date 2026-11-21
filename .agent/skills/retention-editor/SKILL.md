---
name: retention-editor
description: Apply retention engineering presets extracted from GTAClipper, OpenShorts, and Cutawan—0.30s silence cutting, 1.06x asymmetric punch-ins, 3-word hook banners, and safe-zone word-by-word ASS captions.
---

# Retention Editor (Phase 3)

## Extracted Best-in-Class Presets (`config/presets.json`)
1. **Caption Presets (ASS / libass burn-in):**
   - `gtaclipper_pill` (Default for GTA 6): `Poppins-ExtraBold` / `DejaVu Sans Bold` fallback, uppercase, `#FFFFFF` base, `#111111` active word over `#FFC400` yellow pill (`BorderStyle=3`), `#000000` 4px outline, max 3 words/line, `position_y=0.74`.
   - `cutawan_beast`: `Anton`, `fontScale=0.052`, uppercase, `#FFFFFF` base, `#FFD400` active highlight, `outlineWidth=3.2`, `position_y=0.72`, `wordsPerGroup=3`.
   - `openshorts_karaoke`: `Poppins`, `fontScale=0.046`, `#FFFFFF` base, `#4ADE80` active highlight, `position_y=0.74`, `wordsPerGroup=4`.
2. **Word Grouping Guardrails (from Cutawan `captionLayout.ts`):**
   - Respect `POP_ALLOWANCE_EM = 0.5` and glyph width budgets (`Anton: 0.46em`, `Poppins: 0.65em`) so active-word pop never overflows the 9:16 safe width or wraps to a 3rd line.
   - Break groups on pauses > 0.35s, sentence boundaries, or word cap.
   - **Important FFmpeg Filter Rule (from OpenShorts `subtitles.py`):** Never put apostrophes inside ASS file paths passed to `ass='...'`; always use neutral filenames (`subs_clip_01.ass`).
3. **Retention Polish:**
   - **Silence Jump-Cuts:** Trim dead air gaps > `0.30s` using FFmpeg `silencedetect` / `faster-whisper` word timestamps.
   - **Reaction Punch-Ins:** Apply OpenShorts `punch_in.py` smoothstep curve at `MAX_ZOOM = 1.06`, `RISE = 0.25s`, `HOLD = 1.30s`, `FALL = 0.55s`, `MIN_GAP = 18.0s` (never twitch-zoom > 3/min).
   - **Burned-In Subtitle Guard (from OpenShorts `burned_subtitles.py`):** If source has pre-burned bottom captions in `0.75..0.97` height band, crop above band before rendering clean vertical captions.
   - **3-Word Hook Banner:** Top safe zone (`y = 14%` of 1920px = ~268px), high-contrast pill/box visible `0.0s–3.0s`.
4. **Automated Quality Gate (`src/gta6_clipper/quality_gate.py`):**
   - `ffprobe` must verify `1080x1920`, `15.0 <= duration <= 58.0`, audio stream present, `|video_dur - audio_dur| < 0.35s`, and zero watermarks before publishing.
