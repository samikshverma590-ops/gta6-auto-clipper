"""Phase 0: Self-provisioning audit & human login checkpoint."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, asdict
from typing import List

from .config import AppConfig

REQUIRED_SKILLS = [
    "viral-trend-researcher",
    "hybrid-clip-orchestrator",
    "retention-editor",
    "omni-publisher",
    "self-learning-optimizer",
    "openshorts",
    "postiz",
]


@dataclass
class CheckpointItem:
    name: str
    category: str
    configured: bool
    setup_url: str
    notes: str


def run_phase0_audit(cfg: AppConfig) -> dict:
    """Verify .agent/skills/, memory files, local/cloud binaries, and credentials."""
    missing_skills = [
        s for s in REQUIRED_SKILLS if not (cfg.skills_dir / s / "SKILL.md").is_file()
    ]
    memory_files = {
        "viral_playbook": (cfg.memory_dir / "viral_playbook.json").is_file(),
        "processed_history": (cfg.memory_dir / "processed_history.json").is_file(),
        "mistakes_and_lessons": (cfg.memory_dir / "mistakes_and_lessons.json").is_file(),
    }
    tools = {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
        "yt-dlp": shutil.which("yt-dlp") is not None,
    }

    has_ai_scorer = bool(cfg.gemini_api_key or cfg.llm_base_url)
    has_youtube_auth = bool(
        cfg.youtube_client_secrets_json and cfg.youtube_refresh_token
    )
    has_social_bridge = bool(
        cfg.upload_post_api_key
        or (cfg.trypost_api_url and cfg.trypost_api_key)
        or (cfg.postiz_api_url and cfg.postiz_api_key)
    )
    has_direct_tiktok_ig = bool(
        cfg.tiktok_session_id or (cfg.ig_access_token and cfg.ig_user_id)
    )

    checklist: List[CheckpointItem] = [
        CheckpointItem(
            name="Google AI Studio Gemini API Key (Free Tier)",
            category="AI Scoring",
            configured=has_ai_scorer,
            setup_url="https://aistudio.google.com/app/apikey",
            notes="Set GEMINI_API_KEY in GitHub Secrets (or LLM_BASE_URL for free Ollama/Groq). Heuristic scorer acts as offline fallback.",
        ),
        CheckpointItem(
            name="YouTube Data API v3 OAuth (Free Quota)",
            category="Publishing",
            configured=has_youtube_auth,
            setup_url="https://console.cloud.google.com/apis/credentials",
            notes="Set YOUTUBE_CLIENT_SECRETS_JSON and YOUTUBE_REFRESH_TOKEN in GitHub Secrets for direct YouTube Shorts uploads.",
        ),
        CheckpointItem(
            name="Multi-Platform Social MCP (TryPost / Postiz / Upload-Post)",
            category="Publishing",
            configured=has_social_bridge,
            setup_url="https://github.com/gitroomhq/postiz-app",
            notes="Optional $0 self-hosted TryPost/Postiz instance or Upload-Post free tier for TikTok + IG + YouTube.",
        ),
        CheckpointItem(
            name="YouTube Cookies for Cloud Slice Downloads (Free)",
            category="Discovery & Clipping",
            configured=bool(cfg.youtube_cookies_txt),
            setup_url="https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies",
            notes="Export cookies.txt from a secondary YouTube account and save as GitHub Secret YOUTUBE_COOKIES_TXT so TechnoGamerz/CarryMinati/MrBeast/IShowSpeed slices download at 1080p without 403.",
        ),
        CheckpointItem(
            name="TikTok / Instagram Direct Session (Optional)",
            category="Publishing",
            configured=has_direct_tiktok_ig,
            setup_url="https://developers.facebook.com/docs/instagram-api/",
            notes="Needed only if publishing directly to TikTok/IG without TryPost/Postiz/Upload-Post.",
        ),
    ]

    return {
        "missing_skills": missing_skills,
        "memory_files": memory_files,
        "tools": tools,
        "ready_for_live_publish": has_youtube_auth
        or has_social_bridge
        or has_direct_tiktok_ig,
        "checkpoint_items": [asdict(item) for item in checklist],
    }


def format_checkpoint_markdown(audit: dict) -> str:
    lines = ["## Phase 0 Audit & Login Checkpoint", ""]
    lines.append("### Skills & Memory")
    total_skills = len(REQUIRED_SKILLS)
    lines.append(
        f"- Missing skills: {', '.join(audit['missing_skills']) or f'None ({total_skills}/{total_skills} present)'}"
    )
    for k, ok in audit["memory_files"].items():
        lines.append(f"- `memory/{k}.json`: {'OK' if ok else 'MISSING'}")
    lines.append("")
    lines.append("### Ephemeral Runner Binaries")
    for k, ok in audit["tools"].items():
        lines.append(f"- `{k}`: {'Installed' if ok else 'Missing (auto-installed in GitHub Actions)'}")
    lines.append("")
    lines.append("### Credential Checklist (GitHub Repository Secrets)")
    for item in audit["checkpoint_items"]:
        status = "✅ Configured" if item["configured"] else "⏳ Action needed for live publish"
        lines.append(f"- **{item['name']}** — {status}")
        lines.append(f"  - Portal: {item['setup_url']}")
        lines.append(f"  - {item['notes']}")
    return "\n".join(lines)
