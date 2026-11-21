"""Phase 5: Self-learning optimizer & flop post-mortem analyzer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import httpx

from .config import AppConfig, load_json, save_json


def _duration_bucket(sec: float) -> str:
    if sec <= 28.0:
        return "short_punch"
    if sec <= 45.0:
        return "mid_story"
    return "deep_breakdown"


def _sync_upload_post_metrics(clips: list, cfg: AppConfig) -> int:
    """Pull 48h+ analytics from Upload-Post when request_id is tracked."""
    if not cfg.upload_post_api_key:
        return 0
    now = datetime.now(timezone.utc)
    synced = 0
    for clip in clips:
        if clip.get("metrics_48h") or not clip.get("request_id"):
            continue
        sched_str = clip.get("scheduled_at")
        if sched_str:
            try:
                dt = datetime.fromisoformat(sched_str.replace("Z", "+00:00"))
                if (now - dt).total_seconds() < 48 * 3600:
                    continue
            except ValueError:
                pass
        try:
            resp = httpx.get(
                f"https://api.upload-post.com/api/uploadposts/post-analytics/{clip['request_id']}",
                headers={"Authorization": f"Apikey {cfg.upload_post_api_key}"},
                timeout=15.0,
            )
            if resp.status_code != 200:
                continue
            payload = resp.json()
            total_views = 0
            for item in (payload.get("analytics") or payload.get("platforms") or []):
                if isinstance(item, dict):
                    total_views += int(item.get("views", 0) or 0)
            if isinstance(payload.get("views"), int):
                total_views = max(total_views, payload["views"])
            clip["metrics_48h"] = {"views": total_views, "raw": payload}
            synced += 1
        except Exception:
            continue
    return synced


def run_learning_cycle(cfg: AppConfig) -> dict:
    """Evaluate 48h clip metrics, boost winners (>30% above avg), and log flop lessons."""
    playbook_path = cfg.memory_dir / "viral_playbook.json"
    history_path = cfg.memory_dir / "processed_history.json"
    lessons_path = cfg.memory_dir / "mistakes_and_lessons.json"

    playbook = load_json(playbook_path, {})
    history = load_json(history_path, {})
    lessons = load_json(
        lessons_path,
        {
            "schema_version": "1.0.0",
            "lessons": [],
            "flop_postmortems": [],
            "tool_recoveries": [],
        },
    )

    clips = history.get("published_clips", [])
    synced_count = _sync_upload_post_metrics(clips, cfg)
    channel_avg = float(
        playbook.get("channel_stats", {}).get("channel_avg_views_48h", 1500)
    )
    boosted: List[str] = []
    flops: List[dict] = []

    evaluated_views: List[int] = []
    for clip in clips:
        metrics = clip.get("metrics_48h")
        if not metrics or clip.get("evaluated"):
            continue
        views = int(metrics.get("views", 0))
        evaluated_views.append(views)

        arch_id = clip.get("hook_archetype")
        preset = clip.get("caption_preset")
        bucket = _duration_bucket(float(clip.get("duration_sec", 30.0)))

        if views > channel_avg * 1.30:
            for arch in playbook.get("hook_archetypes", []):
                if arch["id"] == arch_id:
                    arch["weight"] = round(
                        min(2.0, float(arch.get("weight", 1.0)) + 0.15), 2
                    )
                    boosted.append(f"hook:{arch_id}->{arch['weight']}")
            preset_weights = playbook.setdefault("pacing_and_duration", {}).setdefault(
                "caption_preset_weights", {}
            )
            if preset:
                preset_weights[preset] = round(
                    min(2.0, float(preset_weights.get(preset, 1.0)) + 0.12), 2
                )
                boosted.append(f"preset:{preset}->{preset_weights[preset]}")
            buckets = playbook.setdefault("pacing_and_duration", {}).setdefault(
                "optimal_duration_buckets", {}
            )
            if bucket in buckets:
                buckets[bucket]["weight"] = round(
                    min(2.0, float(buckets[bucket].get("weight", 1.0)) + 0.10), 2
                )
                boosted.append(f"duration:{bucket}->{buckets[bucket]['weight']}")
        elif views < 500:
            diagnosis = (
                "weak_0_3s_hook_or_saturation"
                if float(clip.get("duration_sec", 30)) <= 30
                else "pacing_drop_excess_duration"
            )
            pm = {
                "clip_id": clip.get("clip_id"),
                "views_48h": views,
                "hook_archetype": arch_id,
                "caption_preset": preset,
                "duration_sec": clip.get("duration_sec"),
                "diagnosis": diagnosis,
                "rule": f"Deprioritize {arch_id}/{bucket} unless hook_impact >= 30/35",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            lessons.setdefault("flop_postmortems", []).append(pm)
            flops.append(pm)
            for arch in playbook.get("hook_archetypes", []):
                if arch["id"] == arch_id:
                    arch["weight"] = round(
                        max(0.5, float(arch.get("weight", 1.0)) - 0.10), 2
                    )

        clip["evaluated"] = True

    if evaluated_views:
        prev_n = int(playbook.get("channel_stats", {}).get("total_evaluated_clips", 0))
        new_n = prev_n + len(evaluated_views)
        new_avg = round(
            (channel_avg * prev_n + sum(evaluated_views)) / max(1, new_n), 1
        )
        playbook.setdefault("channel_stats", {})["channel_avg_views_48h"] = new_avg
        playbook["channel_stats"]["total_evaluated_clips"] = new_n

    playbook["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(playbook_path, playbook)
    save_json(history_path, history)
    save_json(lessons_path, lessons)

    if boosted:
        skill_file = cfg.skills_dir / "retention-editor" / "SKILL.md"
        if skill_file.exists():
            note = (
                f"\n- Auto-learned boost ({datetime.now(timezone.utc).date()}): "
                + ", ".join(boosted)
            )
            content = skill_file.read_text(encoding="utf-8")
            if "## Learned Weight Adjustments" not in content:
                content += "\n\n## Learned Weight Adjustments"
            content += note + "\n"
            skill_file.write_text(content, encoding="utf-8")

    return {
        "synced_from_upload_post": synced_count,
        "evaluated_count": len(evaluated_views),
        "boosted_weights": boosted,
        "flops_logged": len(flops),
    }
