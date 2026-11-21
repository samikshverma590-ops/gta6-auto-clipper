"""Configuration and JSON memory store helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def find_repo_root(start: Path | None = None) -> Path:
    """Locate repository root containing config/ and memory/."""
    cur = (start or Path(__file__)).resolve()
    for parent in [cur, *cur.parents]:
        if (parent / "config").is_dir() and (parent / "memory").is_dir():
            return parent
    return Path.cwd().resolve()


@dataclass(frozen=True)
class AppConfig:
    repo_root: Path
    work_dir: Path
    gemini_api_key: str
    gemini_model: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    openshorts_mcp_url: str
    openshorts_api_key: str
    gtaclipper_mcp_url: str
    trypost_api_url: str
    trypost_api_key: str
    postiz_api_url: str
    postiz_api_key: str
    upload_post_api_key: str
    upload_post_profile: str
    youtube_client_secrets_json: str
    youtube_refresh_token: str
    tiktok_session_id: str
    ig_access_token: str
    ig_user_id: str
    dry_run: bool
    min_viral_score: int
    stagger_hours: float

    @property
    def config_dir(self) -> Path:
        return self.repo_root / "config"

    @property
    def memory_dir(self) -> Path:
        return self.repo_root / "memory"

    @property
    def skills_dir(self) -> Path:
        return self.repo_root / ".agent" / "skills"


def load_config(repo_root: Path | None = None) -> AppConfig:
    root = find_repo_root(repo_root)
    work_dir = Path(os.environ.get("CLIP_WORK_DIR", "/tmp/clip_work")).resolve()
    # Guardrail: never allow CLIP_WORK_DIR inside the git repo itself.
    if root in work_dir.parents or work_dir == root:
        work_dir = Path("/tmp/clip_work").resolve()

    return AppConfig(
        repo_root=root,
        work_dir=work_dir,
        gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip(),
        llm_base_url=os.environ.get("LLM_BASE_URL", "").strip(),
        llm_api_key=os.environ.get("LLM_API_KEY", "").strip(),
        llm_model=os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile").strip(),
        openshorts_mcp_url=os.environ.get(
            "OPENSHORTS_MCP_URL", "https://mcp.openshorts.app/mcp"
        ).strip(),
        openshorts_api_key=os.environ.get("OPENSHORTS_API_KEY", "").strip(),
        gtaclipper_mcp_url=os.environ.get(
            "GTACLIPPER_MCP_URL", "http://127.0.0.1:3107/mcp"
        ).strip(),
        trypost_api_url=os.environ.get("TRYPOST_API_URL", "").strip(),
        trypost_api_key=os.environ.get("TRYPOST_API_KEY", "").strip(),
        postiz_api_url=os.environ.get("POSTIZ_API_URL", "").strip(),
        postiz_api_key=os.environ.get("POSTIZ_API_KEY", "").strip(),
        upload_post_api_key=os.environ.get("UPLOAD_POST_API_KEY", "").strip(),
        upload_post_profile=os.environ.get("UPLOAD_POST_PROFILE", "gta6clips").strip(),
        youtube_client_secrets_json=os.environ.get(
            "YOUTUBE_CLIENT_SECRETS_JSON", ""
        ).strip(),
        youtube_refresh_token=os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip(),
        tiktok_session_id=os.environ.get("TIKTOK_SESSION_ID", "").strip(),
        ig_access_token=os.environ.get("IG_ACCESS_TOKEN", "").strip(),
        ig_user_id=os.environ.get("IG_USER_ID", "").strip(),
        dry_run=os.environ.get("DRY_RUN", "true").strip().lower()
        not in {"0", "false", "no"},
        min_viral_score=int(os.environ.get("MIN_VIRAL_SCORE", "85")),
        stagger_hours=float(os.environ.get("STAGGER_HOURS", "3.5")),
    )


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_sources(config: AppConfig) -> list[str]:
    sources_file = config.config_dir / "sources.txt"
    if not sources_file.exists():
        return []
    lines: list[str] = []
    for raw in sources_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines
