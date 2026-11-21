---
name: omni-publisher
description: Format platform-native metadata, stagger release schedules by 3-4 hours, publish via free/open-source MCPs (TryPost, Postiz, OpenShorts/Upload-Post, YouTube Data API v3), and wipe /tmp/clip_work/.
---

# Omni-Publisher (Phase 4)

## Supported $0 Publishing Backends (`src/gta6_clipper/publisher.py`)
1. **TryPost (`trypostit/trypost`) / Postiz (`gitroomhq/postiz-app`):** Self-hosted open-source MCP & REST social schedulers (unlimited channels at $0).
2. **OpenShorts / Upload-Post (`social_platforms.py` schema):** Free-tier REST/MCP bridge supporting `tiktok_title`, `instagram_title` (`media_type=REELS`), and `youtube_title` (<=100 chars) + `youtube_description` (`privacyStatus=public`).
3. **Direct YouTube Data API v3 OAuth:** 100% free Google API quota using `YOUTUBE_CLIENT_SECRETS_JSON` + `YOUTUBE_REFRESH_TOKEN`.

## Native Platform Formatting Rules
- **YouTube Shorts:** Title strictly `<= 100` chars (word-boundary truncated), `#Shorts #GTA6 #GTAVI` in description, pinned engagement question in first comment/description.
- **TikTok:** Hook-first punchy caption (`<= 150` chars recommended), 4–5 targeted hashtags (`#gta6 #gtavi #vicecity #leonida #gaming`), `MEDIA_UPLOAD` mode.
- **Instagram Reels:** Save-worthy curiosity caption + engagement question + 5–8 niche hashtags (`#gta6 #gtavi #rockstargames #vicecity #gamingreels`).

## Staggered Scheduling & Zero-Storage Hygiene
- Stagger publish timestamps by **3.5 hours** (configurable `3.0–4.0h`) across platforms/clips to avoid duplicate-burst spam filters and hit separate feed cycles.
- **Mandatory Wipe:** Immediately after upload/schedule (or dry-run manifest write), delete `/tmp/clip_work/` completely (`shutil.rmtree`) and verify zero `.mp4`/`.mkv` artifacts remain anywhere in the workspace or `/tmp/clip_work/`.
