"""Phase 4: Multi-platform staggered publisher & mandatory /tmp/clip_work/ wipe."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import httpx

from .config import AppConfig, load_json, save_json
from .orchestrator import RenderedClip

SUPPORTED_SHORT_PLATFORMS = ("youtube", "tiktok", "instagram")


def truncate_youtube_title(title: str, max_len: int = 100) -> str:
    """Truncate title on word boundary to YouTube's 100-character limit."""
    t = " ".join(str(title or "").split())
    if len(t) <= max_len:
        return t
    cut = t[:max_len]
    space = cut.rfind(" ")
    return (cut[:space] if space >= max_len // 2 else cut).rstrip(" ,.;:-")


def build_platform_payload(
    clip: RenderedClip, playbook: dict, scheduled_iso: str
) -> Dict[str, dict]:
    """Generate native metadata payloads for YouTube Shorts, TikTok, and Instagram Reels."""
    templates = playbook.get("platform_metadata_templates", {})
    yt_tpl = templates.get("youtube", {})
    tt_tpl = templates.get("tiktok", {})
    ig_tpl = templates.get("instagram", {})

    question = clip.pinned_question or (
        yt_tpl.get("pinned_question_templates") or ["Rate this GTA 6 clip 1-10!"]
    )[0]
    custom_tags = [t for t in clip.hashtags if str(t).startswith("#")]
    yt_tags = " ".join(custom_tags or yt_tpl.get("hashtags", ["#Shorts", "#GTA6", "#GTAVI"]))
    tt_tags = " ".join(custom_tags or tt_tpl.get("hashtags", ["#gta6", "#gtavi", "#vicecity"]))
    ig_tags = " ".join(custom_tags or ig_tpl.get("hashtags", ["#gta6", "#gtavi", "#gamingreels"]))

    raw_yt_title = clip.youtube_title or f"{clip.title_idea} {yt_tpl.get('title_suffix', '#Shorts')}"
    if "#shorts" not in raw_yt_title.lower():
        raw_yt_title = f"{raw_yt_title} #Shorts"
    yt_title = truncate_youtube_title(raw_yt_title)

    credit = f"Credit: {clip.channel}\n\n" if clip.channel else ""
    yt_desc = f"{clip.hook_banner} — {clip.title_idea}\n{credit}{question}\n\n{yt_tags}"
    tt_base = clip.tiktok_caption or f"{clip.hook_banner}: {clip.title_idea}"
    tt_caption = f"{tt_base} {question} {tt_tags}"[:300]
    ig_base = clip.instagram_caption or f"{clip.hook_banner} 🔥\n{clip.title_idea}"
    ig_caption = f"{ig_base}\n{credit}{question}\n\n{ig_tags}"[:2200]

    return {
        "youtube": {
            "title": yt_title,
            "description": yt_desc,
            "first_comment": question,
            "privacyStatus": "public",
            "categoryId": "20",
            "publishAt": scheduled_iso,
        },
        "tiktok": {
            "caption": tt_caption,
            "post_mode": "MEDIA_UPLOAD",
            "scheduled_at": scheduled_iso,
        },
        "instagram": {
            "caption": ig_caption,
            "media_type": "REELS",
            "scheduled_at": scheduled_iso,
        },
    }


def resolve_upload_post_targets(cfg: AppConfig) -> Tuple[str, List[str]]:
    """Query Upload-Post /uploadposts/users to find active profile & connected platforms."""
    fallback_profile = cfg.upload_post_profile or "default"
    if not cfg.upload_post_api_key:
        return fallback_profile, list(SUPPORTED_SHORT_PLATFORMS)
    try:
        resp = httpx.get(
            "https://api.upload-post.com/api/uploadposts/users",
            headers={"Authorization": f"Apikey {cfg.upload_post_api_key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        profiles = resp.json().get("profiles", [])
        chosen = next(
            (p for p in profiles if p.get("username") == fallback_profile and not p.get("blocked")),
            next((p for p in profiles if not p.get("blocked")), None),
        )
        if not chosen:
            return fallback_profile, []
        accounts = chosen.get("social_accounts") or {}
        connected = [
            p
            for p in SUPPORTED_SHORT_PLATFORMS
            if isinstance(accounts.get(p), dict)
            and not accounts[p].get("reauth_required", False)
        ]
        return chosen.get("username", fallback_profile), connected
    except Exception:
        return fallback_profile, ["youtube"]


def _publish_via_upload_post(
    clip: RenderedClip,
    payloads: Dict[str, dict],
    cfg: AppConfig,
    schedule_future: bool,
) -> dict | None:
    if not cfg.upload_post_api_key:
        return None
    video_path = Path(clip.output_path)
    if not video_path.is_file():
        return None

    profile, connected_platforms = resolve_upload_post_targets(cfg)
    if not connected_platforms:
        return {"skipped": "no_connected_upload_post_platforms", "profile": profile}

    data: dict[str, object] = {
        "user": profile,
        "title": payloads["youtube"]["title"],
        "async_upload": "true",
        "platform[]": connected_platforms,
    }

    if "youtube" in connected_platforms:
        data.update(
            {
                "youtube_title": payloads["youtube"]["title"],
                "youtube_description": payloads["youtube"]["description"],
                "youtube_first_comment": payloads["youtube"]["first_comment"],
                "privacyStatus": payloads["youtube"]["privacyStatus"],
                "categoryId": payloads["youtube"]["categoryId"],
            }
        )
    if "tiktok" in connected_platforms:
        data.update(
            {
                "tiktok_title": payloads["tiktok"]["caption"],
                "post_mode": payloads["tiktok"]["post_mode"],
            }
        )
    if "instagram" in connected_platforms:
        data.update(
            {
                "instagram_title": payloads["instagram"]["caption"],
                "media_type": payloads["instagram"]["media_type"],
            }
        )
    if schedule_future:
        data["scheduled_date"] = payloads["youtube"]["publishAt"]

    with video_path.open("rb") as fh:
        resp = httpx.post(
            "https://api.upload-post.com/api/upload",
            headers={"Authorization": f"Apikey {cfg.upload_post_api_key}"},
            data=data,
            files={"video": (video_path.name, fh, "video/mp4")},
            timeout=180.0,
        )
    resp.raise_for_status()
    result = resp.json()
    result["profile"] = profile
    result["platforms"] = connected_platforms
    return result


def _publish_via_trypost_or_postiz(
    clip: RenderedClip, payloads: Dict[str, dict], cfg: AppConfig
) -> dict | None:
    if cfg.postiz_api_url and cfg.postiz_api_key:
        base, key = cfg.postiz_api_url.rstrip("/"), cfg.postiz_api_key
        provider = "postiz"
    elif cfg.trypost_api_url and cfg.trypost_api_key:
        base, key = cfg.trypost_api_url.rstrip("/"), cfg.trypost_api_key
        provider = "trypost"
    else:
        return None

    resp = httpx.post(
        f"{base}/public/v1/posts",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "type": "schedule",
            "date": payloads["youtube"]["publishAt"],
            "tags": [],
            "posts": [
                {"platform": "youtube", **payloads["youtube"]},
                {"platform": "tiktok", **payloads["tiktok"]},
                {"platform": "instagram", **payloads["instagram"]},
            ],
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return {"provider": provider, "response": resp.json()}


def wipe_work_dir(work_dir: Path) -> bool:
    """Completely delete /tmp/clip_work/ so zero media remains anywhere."""
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    return not work_dir.exists()


def publish_and_cleanup(clips: List[RenderedClip], cfg: AppConfig) -> dict:
    """Publish/schedule verified clips with 3-4h stagger and wipe /tmp/clip_work/."""
    playbook = load_json(cfg.memory_dir / "viral_playbook.json", {})
    history_path = cfg.memory_dir / "processed_history.json"
    history = load_json(
        history_path,
        {
            "schema_version": "1.0.0",
            "processed_videos": [],
            "published_clips": [],
            "stats": {},
        },
    )

    now = datetime.now(timezone.utc)
    results: List[dict] = []

    for idx, clip in enumerate(clips):
        if not clip.quality_passed:
            continue
        sched_dt = now + timedelta(hours=idx * cfg.stagger_hours)
        sched_iso = sched_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        payloads = build_platform_payload(clip, playbook, sched_iso)

        status = "dry_run_staged"
        provider = "dry_run"
        request_id = None
        published_platforms: List[str] = []

        if not cfg.dry_run:
            try:
                api_resp = _publish_via_trypost_or_postiz(clip, payloads, cfg)
                if api_resp:
                    provider = api_resp["provider"]
                    status = "scheduled"
                else:
                    api_resp = _publish_via_upload_post(
                        clip, payloads, cfg, schedule_future=(idx > 0)
                    )
                    if api_resp:
                        provider = "upload_post"
                        request_id = api_resp.get("request_id") or api_resp.get("job_id")
                        published_platforms = api_resp.get("platforms", [])
                        status = (
                            "skipped_no_accounts"
                            if api_resp.get("skipped")
                            else ("scheduled" if idx > 0 else "published_or_queued")
                        )
            except Exception as exc:
                status = f"error:{exc}"

        record = {
            "clip_id": clip.clip_id,
            "video_id": clip.video_id,
            "source_url": clip.video_url,
            "engine_used": clip.engine_used,
            "score": clip.score,
            "hook_archetype": clip.hook_archetype,
            "hook_banner": clip.hook_banner,
            "caption_preset": clip.caption_preset,
            "duration_sec": clip.duration,
            "scheduled_at": sched_iso,
            "provider": provider,
            "request_id": request_id,
            "published_platforms": published_platforms,
            "status": status,
            "platforms": payloads,
            "metrics_48h": None,
            "evaluated": False,
        }
        if not status.startswith("error:"):
            history.setdefault("published_clips", []).append(record)
            if clip.video_id not in {
                v.get("video_id") for v in history.get("processed_videos", [])
            }:
                history.setdefault("processed_videos", []).append(
                    {
                        "video_id": clip.video_id,
                        "source_url": clip.video_url,
                        "processed_at": now.isoformat(),
                    }
                )
        results.append(record)

    stats = history.setdefault("stats", {})
    stats["total_videos_scanned"] = len(history.get("processed_videos", []))
    stats["total_clips_rendered"] = stats.get("total_clips_rendered", 0) + len(clips)
    stats["total_clips_published"] = len(history.get("published_clips", []))
    stats["last_run_utc"] = now.isoformat()
    save_json(history_path, history)

    wiped = wipe_work_dir(cfg.work_dir)
    return {
        "published_count": len(results),
        "dry_run": cfg.dry_run,
        "work_dir_wiped": wiped,
        "records": results,
    }
