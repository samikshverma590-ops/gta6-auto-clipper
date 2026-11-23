"""Phase 3: Hybrid clip orchestrator — routes between GTAClipper, OpenShorts, and FFmpeg
with pre-render Whisper audio safety & intelligibility verification.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import httpx

from .config import AppConfig, ensure_cookies_file, load_json, save_json
from .editor import extract_word_timings, render_vertical_clip
from .quality_gate import validate_clip
from .scorer import ScoredClip, contains_unsafe_language


@dataclass
class RenderedClip:
    clip_id: str
    video_id: str
    video_url: str
    output_path: str
    engine_used: str
    score: int
    hook_archetype: str
    hook_banner: str
    title_idea: str
    caption_preset: str
    duration: float
    quality_passed: bool
    channel: str = ""
    youtube_title: str = ""
    tiktok_caption: str = ""
    instagram_caption: str = ""
    hashtags: List[str] = field(default_factory=list)
    pinned_question: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def choose_engine(clip: ScoredClip, cfg: AppConfig) -> str:
    """Dynamically pick optimal engine for stream type."""
    if clip.has_webcam_hint:
        return "gtaclipper_split"
    if cfg.openshorts_api_key:
        return "openshorts_mcp"
    return "openshorts_local_ffmpeg"


def download_slice(
    clip: ScoredClip, out_path: Path, cookies_file: Path | None = None
) -> Path:
    """Download ONLY the winning timestamp slice (or pre-cut Twitch clip) via yt-dlp."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if "twitch.tv/" in clip.video_url and "/clip/" in clip.video_url:
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--merge-output-format",
            "mp4",
            "-o",
            str(out_path),
            clip.video_url,
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        return out_path

    section = f"*{clip.start:.2f}-{clip.end:.2f}"
    js_flags = ["--js-runtimes", "node"] if shutil.which("node") else []
    cookie_flags = ["--cookies", str(cookies_file)] if cookies_file else []
    base_cmd = [
        "yt-dlp",
        "-4",
        *js_flags,
        *cookie_flags,
        "--download-sections",
        section,
        "-f",
        "bestvideo[height<=1080][protocol^=m3u8]+bestaudio[protocol^=m3u8]/best[height<=1080][protocol^=m3u8]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best",
        "--merge-output-format",
        "mp4",
        "--no-playlist",
        "-o",
        str(out_path),
    ]
    attempts = (
        [[*base_cmd, "--force-keyframes-at-cuts", clip.video_url]]
        if cookies_file
        else [
            [*base_cmd, "--extractor-args", "youtube:player_client=visionos", clip.video_url],
            [*base_cmd, "--force-keyframes-at-cuts", clip.video_url],
            [*base_cmd, "--extractor-args", "youtube:player_client=android_vr,tv", clip.video_url],
        ]
    )
    last_err: subprocess.CalledProcessError | None = None
    for cmd in attempts:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
            return out_path
        except subprocess.CalledProcessError as exc:
            last_err = exc
            if out_path.exists():
                out_path.unlink(missing_ok=True)
    assert last_err is not None
    raise last_err


def _log_recovery(cfg: AppConfig, engine: str, error_msg: str, fallback: str) -> None:
    lessons_path = cfg.memory_dir / "mistakes_and_lessons.json"
    data = load_json(
        lessons_path,
        {
            "schema_version": "1.0.0",
            "lessons": [],
            "flop_postmortems": [],
            "tool_recoveries": [],
        },
    )
    data.setdefault("tool_recoveries", []).append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "failed_engine": engine,
            "error": error_msg[-350:],
            "fallback_applied": fallback,
        }
    )
    save_json(lessons_path, data)


def _try_openshorts_mcp(clip: ScoredClip, cfg: AppConfig) -> bool:
    """Optional check against OpenShorts MCP endpoint if API key is provided."""
    if not cfg.openshorts_api_key:
        return False
    try:
        resp = httpx.post(
            cfg.openshorts_mcp_url,
            headers={"Authorization": f"Bearer {cfg.openshorts_api_key}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            timeout=10.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


def process_scored_clips(
    clips: List[ScoredClip], cfg: AppConfig, max_clips: int = 3
) -> List[RenderedClip]:
    """Download slice-only media into /tmp/clip_work/, transcribe & verify safety, render 9:16."""
    raw_dir = cfg.work_dir / "raw"
    out_dir = cfg.work_dir / "rendered"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    cookies_file = ensure_cookies_file(cfg)

    rendered: List[RenderedClip] = []
    seen_videos: set[str] = set()
    primary: List[ScoredClip] = []
    backups: List[ScoredClip] = []
    for c in clips:
        if c.video_id not in seen_videos:
            primary.append(c)
            seen_videos.add(c.video_id)
        else:
            backups.append(c)
    ordered = primary + backups

    for idx, clip in enumerate(ordered, start=1):
        if len(rendered) >= max_clips:
            break
        clip_id = f"{clip.video_id}_{int(clip.start)}_{int(clip.end)}"
        raw_file = raw_dir / f"slice_{idx:02d}_{clip.video_id}.mp4"
        out_file = out_dir / f"clip_{idx:02d}_{clip.video_id}.mp4"

        engine = choose_engine(clip, cfg)
        try:
            download_slice(clip, raw_file, cookies_file=cookies_file)
        except subprocess.CalledProcessError as exc:
            _log_recovery(cfg, "yt-dlp-slice", exc.stderr or str(exc), "skip_clip")
            continue
        except Exception as exc:
            _log_recovery(cfg, "yt-dlp-slice", str(exc), "skip_clip")
            continue

        # Transcribe real spoken audio and reject unsafe profanity or unintelligible clips
        spoken_words = extract_word_timings(
            raw_file, clip.text, clip.duration, allow_fallback=False
        )
        spoken_text = " ".join(w.word for w in spoken_words)
        if contains_unsafe_language(spoken_text):
            _log_recovery(
                cfg,
                "safety-gate",
                f"Rejected unsafe profanity in spoken audio for {clip.video_id}",
                "skip_clip",
            )
            continue
        if len(spoken_words) < 5:
            _log_recovery(
                cfg,
                "clarity-gate",
                f"Too few intelligible spoken words ({len(spoken_words)}) in {clip.video_id}",
                "skip_clip",
            )
            continue

        split_cam = engine == "gtaclipper_split"
        if engine == "openshorts_mcp" and not _try_openshorts_mcp(clip, cfg):
            _log_recovery(
                cfg,
                "openshorts_mcp",
                "MCP unreachable or unauthenticated",
                "ffmpeg_hybrid",
            )
            engine = "ffmpeg_hybrid"

        try:
            render_vertical_clip(
                input_path=raw_file,
                output_path=out_file,
                hook_banner=clip.hook_banner,
                transcript_text=clip.text,
                preset_name=clip.caption_preset,
                split_facecam=split_cam,
                cfg=cfg,
                words_override=spoken_words,
            )
            report = validate_clip(out_file)
            if not report.passed:
                _log_recovery(
                    cfg,
                    engine,
                    "; ".join(report.errors),
                    "ffmpeg_safe_center_fallback",
                )
                fallback_out = out_dir / f"clip_{idx:02d}_{clip.video_id}_fb.mp4"
                render_vertical_clip(
                    input_path=raw_file,
                    output_path=fallback_out,
                    hook_banner=clip.hook_banner,
                    transcript_text=clip.text,
                    preset_name="gtaclipper_pill",
                    split_facecam=False,
                    cfg=cfg,
                    words_override=spoken_words,
                )
                out_file = fallback_out
                engine = "ffmpeg_safe_center_fallback"
                report = validate_clip(out_file)

            rendered.append(
                RenderedClip(
                    clip_id=clip_id,
                    video_id=clip.video_id,
                    video_url=clip.video_url,
                    output_path=str(out_file),
                    engine_used=engine,
                    score=clip.score,
                    hook_archetype=clip.hook_archetype,
                    hook_banner=clip.hook_banner,
                    title_idea=clip.title_idea,
                    caption_preset=clip.caption_preset,
                    duration=report.duration,
                    quality_passed=report.passed,
                    channel=clip.channel,
                    youtube_title=clip.youtube_title,
                    tiktok_caption=clip.tiktok_caption,
                    instagram_caption=clip.instagram_caption,
                    hashtags=clip.hashtags,
                    pinned_question=clip.pinned_question,
                )
            )
        except Exception as exc:
            _log_recovery(cfg, engine, str(exc), "aborted_clip")
            continue

    return rendered
