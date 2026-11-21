"""Unit & integration tests for the GTA 6 Auto-Clipper pipeline."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gta6_clipper.checkpoint import run_phase0_audit
from gta6_clipper.config import AppConfig, load_config, load_json, save_json
from gta6_clipper.discovery import (
    CandidateSegment,
    build_candidate_windows,
    parse_vtt_or_srt,
)
from gta6_clipper.editor import (
    WordTiming,
    build_ass_subtitles,
    build_silence_trim_segments,
    group_words_for_captions,
    hex_to_ass_bgr,
    plan_camera_pan,
    render_vertical_clip,
)
from gta6_clipper.learner import run_learning_cycle
from gta6_clipper.orchestrator import RenderedClip, choose_engine
from gta6_clipper.publisher import (
    build_platform_payload,
    publish_and_cleanup,
    truncate_youtube_title,
)
from gta6_clipper.quality_gate import validate_clip
from gta6_clipper.scorer import score_and_filter_candidates


def _isolated_cfg(tmp_path: Path) -> AppConfig:
    repo = load_config().repo_root
    sandbox = tmp_path / "repo"
    shutil.copytree(repo / "config", sandbox / "config")
    shutil.copytree(repo / "memory", sandbox / "memory")
    shutil.copytree(repo / ".agent", sandbox / ".agent")
    return load_config(sandbox)


def test_phase0_audit_all_skills_and_memory_present():
    cfg = load_config()
    audit = run_phase0_audit(cfg)
    assert audit["missing_skills"] == []
    assert all(audit["memory_files"].values())


def test_vtt_parsing_and_window_building():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:04.500
What! Wait for this insane GTA 6 NPC detail in Vice City!

00:00:04.500 --> 00:00:12.000
Watch what happens when Jason walks up to the car boot and a snake jumps out!

00:00:12.000 --> 00:00:22.000
No way bro, Rockstar actually added snake jump scares inside Leonida cars!
"""
    cues = parse_vtt_or_srt(vtt)
    assert len(cues) == 3
    windows = build_candidate_windows(
        video_id="vid123",
        video_url="https://youtu.be/vid123",
        video_title="GTA 6 Insane NPC & Secret Details!",
        channel="TestChannel",
        cues=cues,
    )
    assert len(windows) >= 1
    assert 15.0 <= windows[0].duration <= 58.0


def test_scorer_threshold_and_policy_guardrail(tmp_path: Path):
    cfg = _isolated_cfg(tmp_path)
    strong = CandidateSegment(
        video_id="v1",
        video_url="https://youtu.be/v1",
        video_title="GTA 6 Insane Secret Detail in Vice City!",
        channel="Ch1",
        start=10.0,
        end=34.0,
        text="What! Wait look at this hidden Rockstar secret in Vice City! Bro no way this NPC just reacted to Jason and Lucia in GTA 6 Leonida!",
        has_webcam_hint=True,
    )
    weak = CandidateSegment(
        video_id="v2",
        video_url="https://youtu.be/v2",
        video_title="Podcast chat",
        channel="Ch2",
        start=0.0,
        end=40.0,
        text="Hey guys welcome back subscribe in today's video we talk slowly about settings.",
        has_webcam_hint=False,
    )
    spoiler = CandidateSegment(
        video_id="v3",
        video_url="https://youtu.be/v3",
        video_title="GTA 6 Final Mission Ending Cutscene Full Cutscene No Commentary",
        channel="Ch3",
        start=0.0,
        end=30.0,
        text="What crazy ending cutscene!",
        has_webcam_hint=False,
    )
    winners = score_and_filter_candidates([strong, weak, spoiler], cfg)
    assert [w.video_id for w in winners] == ["v1"]
    assert winners[0].score >= 85
    assert choose_engine(winners[0], cfg) == "gtaclipper_split"


def test_twitch_clip_scoring_and_engine(tmp_path: Path):
    cfg = _isolated_cfg(tmp_path)
    tw = CandidateSegment(
        video_id="tw_1",
        video_url="https://www.twitch.tv/xqc/clip/SomeViralClip-123",
        video_title="xQc commits Grand Theft Auto for the first time in GTA 6!",
        channel="xqc",
        start=0.0,
        end=34.0,
        text="xqc plays GTA 6: What! Wait look at xQc in GTA 6 Vice City!",
        has_webcam_hint=True,
        view_count=55000,
    )
    winners = score_and_filter_candidates([tw], cfg)
    assert len(winners) == 1
    assert winners[0].score >= 85
    assert choose_engine(winners[0], cfg) == "gtaclipper_split"


def test_cutawan_camera_pan_and_caption_layout(tmp_path: Path):
    cfg = _isolated_cfg(tmp_path)
    presets = load_json(cfg.config_dir / "presets.json")
    pan = plan_camera_pan([0.2, 0.21, 0.22, 0.78, 0.79, 0.80], fps=30.0)
    assert len(pan) == 6
    assert all(0.0 < x < 1.0 for x in pan)

    words = [
        WordTiming("WHAT", 0.0, 0.4),
        WordTiming("A", 0.4, 0.6),
        WordTiming("DETAIL", 0.6, 1.1),
        WordTiming("IN", 1.5, 1.8),
        WordTiming("LEONIDA!", 1.8, 2.3),
    ]
    groups = group_words_for_captions(
        words,
        presets["caption_presets"]["gtaclipper_pill"],
        presets["caption_layout_guardrails"],
    )
    assert len(groups) == 2

    ass_file = build_ass_subtitles(
        words,
        hook_banner="NPC DID WHAT?!",
        preset_name="gtaclipper_pill",
        presets_cfg=presets,
        out_ass_path=tmp_path / "can't_break_ffmpeg.ass",
    )
    assert "'" not in ass_file.name
    text = ass_file.read_text(encoding="utf-8")
    assert "NPC DID WHAT?!" in text
    assert hex_to_ass_bgr("#FFC400") == "&H0000C4FF&"


def test_silence_trim_segments():
    log = """
    [silencedetect @ 0x1] silence_start: 2.000
    [silencedetect @ 0x1] silence_end: 2.800 | silence_duration: 0.800
    """
    kept = build_silence_trim_segments(log, total_duration=10.0, min_silence_sec=0.30)
    assert len(kept) == 2
    assert kept[0][0] == 0.0 and kept[0][1] < 2.2
    assert kept[1][0] > 2.6 and kept[1][1] == 10.0


def test_publisher_stagger_and_disk_wipe(tmp_path: Path):
    cfg = _isolated_cfg(tmp_path)
    work = tmp_path / "ephemeral_work"
    work.mkdir(parents=True)
    (work / "temp.mp4").write_bytes(b"0" * 2048)
    cfg = AppConfig(**{**cfg.__dict__, "work_dir": work})

    clip = RenderedClip(
        clip_id="vid1_0_25",
        video_id="vid1",
        video_url="https://youtu.be/vid1",
        output_path=str(work / "temp.mp4"),
        engine_used="gtaclipper_split",
        score=92,
        hook_archetype="npc_ai_shock",
        hook_banner="NPC DID WHAT?!",
        title_idea="A very long GTA 6 viral title " + ("extra " * 20),
        caption_preset="gtaclipper_pill",
        duration=25.0,
        quality_passed=True,
    )
    playbook = load_json(cfg.memory_dir / "viral_playbook.json")
    payload = build_platform_payload(clip, playbook, "2026-11-21T18:00:00Z")
    assert len(payload["youtube"]["title"]) <= 100
    assert len(truncate_youtube_title("word " * 40)) <= 100

    res = publish_and_cleanup([clip], cfg)
    assert res["published_count"] == 1
    assert res["work_dir_wiped"] is True
    assert not work.exists()


def test_resolve_upload_post_targets_filters_connected(monkeypatch, tmp_path: Path):
    from gta6_clipper import publisher

    cfg = _isolated_cfg(tmp_path)
    cfg = AppConfig(**{**cfg.__dict__, "upload_post_api_key": "test-key", "upload_post_profile": "default"})

    class _FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {
                "success": True,
                "profiles": [
                    {
                        "username": "default",
                        "blocked": False,
                        "social_accounts": {
                            "tiktok": "",
                            "youtube": {"display_name": "SmoothScrolls", "reauth_required": False},
                        },
                    }
                ],
            }

    monkeypatch.setattr(publisher.httpx, "get", lambda *a, **kw: _FakeResp())
    profile, platforms = publisher.resolve_upload_post_targets(cfg)
    assert profile == "default"
    assert platforms == ["youtube"]


def test_upload_post_multipart_serialization(monkeypatch, tmp_path: Path):
    import httpx
    from gta6_clipper import publisher

    cfg = _isolated_cfg(tmp_path)
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00" * 2048)
    cfg = AppConfig(**{**cfg.__dict__, "upload_post_api_key": "k", "dry_run": False})

    monkeypatch.setattr(publisher, "resolve_upload_post_targets", lambda _c: ("default", ["youtube"]))
    captured = {}

    def _fake_post(url, headers=None, data=None, files=None, timeout=None):
        req = httpx.Request("POST", url, headers=headers, data=data, files=files)
        captured["len"] = len(req.read())
        class _R:
            def raise_for_status(self): ...
            def json(self):
                return {"success": True, "request_id": "req-123"}
        return _R()

    monkeypatch.setattr(publisher.httpx, "post", _fake_post)
    clip = RenderedClip(
        clip_id="c1", video_id="v1", video_url="https://twitch.tv/xqc/clip/1",
        output_path=str(vid), engine_used="gtaclipper_split", score=91,
        hook_archetype="npc_ai_shock", hook_banner="NPC ROASTED HIM",
        title_idea="Test", caption_preset="gtaclipper_pill", duration=20.0,
        quality_passed=True, youtube_title="Test #Shorts",
    )
    res = publisher.publish_and_cleanup([clip], cfg)
    assert captured["len"] > 2048
    assert res["records"][0]["request_id"] == "req-123"
    assert res["records"][0]["status"] == "published_or_queued"


def test_learner_boosts_winners_and_logs_flops(tmp_path: Path):
    cfg = _isolated_cfg(tmp_path)
    hist_path = cfg.memory_dir / "processed_history.json"
    hist = load_json(hist_path)
    hist["published_clips"] = [
        {
            "clip_id": "win_1",
            "hook_archetype": "npc_ai_shock",
            "caption_preset": "gtaclipper_pill",
            "duration_sec": 22.0,
            "metrics_48h": {"views": 8000},
            "evaluated": False,
        },
        {
            "clip_id": "flop_1",
            "hook_archetype": "gameplay_mechanic_discovery",
            "caption_preset": "openshorts_karaoke",
            "duration_sec": 50.0,
            "metrics_48h": {"views": 180},
            "evaluated": False,
        },
    ]
    save_json(hist_path, hist)
    summary = run_learning_cycle(cfg)
    assert summary["evaluated_count"] == 2
    assert summary["flops_logged"] == 1
    assert len(summary["boosted_weights"]) >= 1

    lessons = load_json(cfg.memory_dir / "mistakes_and_lessons.json")
    assert len(lessons["flop_postmortems"]) == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ffmpeg_render_and_quality_gate(tmp_path: Path):
    cfg = _isolated_cfg(tmp_path)
    src_mp4 = tmp_path / "synthetic_src.mp4"
    out_mp4 = tmp_path / "synthetic_out.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "16",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(src_mp4),
        ],
        check=True,
        capture_output=True,
    )
    render_vertical_clip(
        input_path=src_mp4,
        output_path=out_mp4,
        hook_banner="NPC DID WHAT?!",
        transcript_text="What a crazy GTA 6 detail in Vice City Leonida!",
        preset_name="gtaclipper_pill",
        split_facecam=True,
        cfg=cfg,
    )
    report = validate_clip(out_mp4)
    assert report.passed, report.errors
    assert report.width == 1080
    assert report.height == 1920

    out_action = tmp_path / "synthetic_action.mp4"
    render_vertical_clip(
        input_path=src_mp4,
        output_path=out_action,
        hook_banner="INSANE DETAIL",
        transcript_text="Look at this dynamic 1.06x vertical action crop in Vice City!",
        preset_name="cutawan_beast",
        split_facecam=False,
        cfg=cfg,
    )
    report2 = validate_clip(out_action)
    assert report2.passed, report2.errors
