---
name: retention-editor
description: Apply retention engineering presets extracted from GTAClipper, OpenShorts, and Cutawan—0.30s silence cutting, 1.06x asymmetric punch-ins, 3-word hook banners, and safe-zone word-by-word ASS captions.
---

# Retention Editor (Phase 3)

## Extracted Best-in-Class Presets (`config/presets.json`)
1. **9:16 Cinema Layout & Smart Framing:**
   - **Blurred Cinema Backdrop:** `1080x1920` dark gaussian-blurred (`gblur=sigma=28`, dimmed) source canvas eliminates black bars and keeps visual energy high.
   - **Split Facecam Mode:** Tight OpenCV frontal + profile facecam detector locates the creator's webcam corner (avoiding chat/UI bleed), renders a sharp `1080x608` top facecam stage at `y=190`, and places a wide `9:8` `1080x960` gameplay stage at `y=810` (with bottom 5% source subtitle bar cropped out).
   - **Pure Action Mode:** `1080x1350` (`4:5`) sharp centered action stage layered cleanly over the blurred cinema backdrop.
   - **3-Word Hook Banner:** Top safe zone (`y=75`), high-contrast dark pill with gold accent border and uppercase text visible `0.0s–3.0s`.
2. **Speech-Synced Caption Pipeline (Zero PyAV Failure):**
   - Decode audio via `ffmpeg -f f32le -ac 1 -ar 16000 pipe:1` -> `np.ndarray` (`float32`) directly into `faster-whisper` (`WhisperModel("base")`), bypassing `av.open(..., metadata_errors=...)` incompatibilities.
   - Apply `-0.10s` caption lead-in so word highlights pop slightly ahead of speech onset; forbid fake title-spread fallback in production (`allow_fallback=False`).
   - Presets (`gtaclipper_pill`, `cutawan_beast`, `openshorts_karaoke`): `Anton` / `Poppins-ExtraBold` / `DejaVu Sans Bold` fallback, uppercase, `#FFFFFF` base, `#FFC400`/`#FFD400`/`#4ADE80` active highlight, max 2–4 words/group (`POP_ALLOWANCE_EM = 0.5`).
   - **Important FFmpeg Filter Rule:** Never put apostrophes inside ASS file paths passed to `ass='...'`; always use neutral filenames (`subs_clip_01.ass`).
3. **Retention Polish & Safety/Clarity Gates:**
   - **Silence Jump-Cuts & Punch-Ins:** Trim dead air gaps > `0.30s`; smoothstep reaction punch-ins capped at `MAX_ZOOM = 1.06`, `MIN_GAP = 18.0s`.
   - **Pre-Render Safety & Clarity Gate:** Transcribe before rendering; reject any clip containing profanity/slurs/NSFW language (`contains_unsafe_language`) or `< 5` intelligible spoken words (`clarity-gate`).
4. **Automated Quality Gate (`src/gta6_clipper/quality_gate.py`):**
   - `ffprobe` must verify `1080x1920`, `15.0 <= duration <= 58.0`, audio stream present, `|video_dur - audio_dur| < 0.35s`, and zero watermarks before publishing.
