---
name: hybrid-clip-orchestrator
description: Dynamically route GTA 6 candidate video slices between GTAClipper, OpenShorts (MIT/MCP), Cutawan-derived FFmpeg camera tracking, and custom FFmpeg+faster-whisper pipelines inside ephemeral /tmp/clip_work/.
---

# Hybrid Clip Orchestrator (Phase 2 & 3)

## Zero-Storage & Zero-Cost Rules
1. **Metadata & Transcripts First:** Fetch transcripts and metadata ONLY via `yt-dlp --write-auto-sub --skip-download --dump-json` into `/tmp/clip_work/subs/`. Never download multi-gigabyte full VODs.
2. **Threshold Gate:** Score every 15–58s candidate segment 0–100 against `memory/viral_playbook.json` (`hook_impact` 35 + `escalation_arc` 25 + `sensory_spike` 20 + `cold_viewer_clarity` 20). Discard `< 85`. Take top 3 per source.
3. **Slice-Only Download:** Download strictly the winning timestamp range with `yt-dlp --download-sections "*START-END"`.

## Engine Routing Matrix (`src/gta6_clipper/orchestrator.py`)
- **Route A (`gtaclipper_split`) — Streamer Facecam / Reaction VODs:**
  - When a webcam overlay or face region is detected in metadata/frame sample, prefer **GTAClipper** (`V413havai/GTAClipper` MCP `/mcp` when running, or native split-stack FFmpeg renderer):
  - Stack **facecam in top 30% (1080x576)** over **GTA 6 gameplay in bottom 70% (1080x1344)**, with yellow-pill word-by-word captions centered near the seam (`y = 0.74`).
- **Route B (`openshorts_action`) — Pure High-Speed Gameplay / Trailer Breakdowns:**
  - Use **OpenShorts** (`mutonby/openshorts` MIT Docker / `openshorts` CLI / `mcp.openshorts.app/mcp`) or our extracted **Cutawan `cameraPath.ts` AutoFlip** stationary-pan planner (`FACE_BAND=0.20`, `SMOOTH_SEC=0.8`, `MIN_HOLD_SEC=1.2`, `PAN_SPEED=0.9`) for stabilized 9:16 vertical action crop (`1080x1920`).
- **Route C (`ffmpeg_fallback`) — Self-Healing Fallback:**
  - If an upstream MCP/Docker service errors or times out, automatically fall back to local `FFmpeg` + `faster-whisper` (`small`, `int8`, `vad_filter=True`) and log the switch to `memory/mistakes_and_lessons.json`.
