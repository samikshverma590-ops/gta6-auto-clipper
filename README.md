# GTA 6 Auto-Clipper

Autonomous, **$0-cost**, **zero-local-storage** GTA 6 short-form clipping and multi-platform publishing pipeline running inside ephemeral GitHub Actions Linux containers.

## Architecture (Phases 0–5)
- **Phase 0 (Self-Provisioning & Skills):** Modular agent skills in [`.agent/skills/`](.agent/skills/) and open-source tool catalog in [`config/mcp_registry.json`](config/mcp_registry.json).
- **Phase 1 (Live Viral Intelligence):** Grounded in GTA 6 launch-week trends (`memory/viral_playbook.json`) and Rockstar Video Policy guardrails.
- **Phase 2 (Zero-Storage Discovery & 100-pt Scoring):** Fetches subtitles & metadata ONLY (`yt-dlp --write-auto-sub --skip-download`), filters processed IDs, and scores segments `0–100` (discarding `< 85`).
- **Phase 3 (Hybrid Cloud Clipping & Quality Gate):** Extracts only winning slices (`--download-sections`) into `/tmp/clip_work/`, combining **GTAClipper** (`gtaclipper_pill` yellow captions, split webcam/gameplay stack), **OpenShorts** (`faster-whisper`, smoothstep `1.06x` punch-in), and **Cutawan** (AutoFlip speaker-lock camera smoothing + glyph-budget word grouping), verified by `ffprobe`.
- **Phase 4 (Omni-Publishing & Disk Wipe):** Formats native metadata for YouTube Shorts, TikTok, and Instagram Reels, staggers release by `3.5h` via free/open-source MCPs (`TryPost`, `Postiz`, `Upload-Post`, or direct YouTube Data API v3), and wipes `/tmp/clip_work/` completely.
- **Phase 5 (Self-Learning Loop):** Evaluates 48h clip performance, boosts winning weights (`> +30%` vs channel avg), and logs flop post-mortems & error recoveries to `memory/mistakes_and_lessons.json`.

## Quick Start (Zero Laptop Storage)
1. Add your free credentials to **GitHub Repository Secrets** (see `.env.example` and run `gta6-clipper audit`).
2. Trigger **Actions → GTA 6 Autonomous Cloud Clipper → Run workflow** (starts in `dry_run=true` by default; set `dry_run=false` once secrets are added).
