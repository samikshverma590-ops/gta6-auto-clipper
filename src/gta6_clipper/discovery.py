"""Phase 2: Zero-storage discovery (metadata + subtitles only, never full VODs)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from typing import Iterable, List

from .config import AppConfig, load_json, load_sources


@dataclass
class TranscriptCue:
    start: float
    end: float
    text: str


@dataclass
class CandidateSegment:
    video_id: str
    video_url: str
    video_title: str
    channel: str
    start: float
    end: float
    text: str
    has_webcam_hint: bool = False
    replay_heat: float = 0.0
    view_count: int = 0

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = self.duration
        return d


_TIMECODE_RE = re.compile(
    r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")


def _to_seconds(h: str | None, m: str, s: str, ms: str) -> float:
    hours = int(h) if h else 0
    return hours * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt_or_srt(content: str) -> List[TranscriptCue]:
    """Parse WebVTT or SRT subtitle text into clean deduplicated cues."""
    cues: List[TranscriptCue] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        m = _TIMECODE_RE.search(lines[i])
        if not m:
            i += 1
            continue
        start = _to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _to_seconds(m.group(5), m.group(6), m.group(7), m.group(8))
        i += 1
        text_parts: List[str] = []
        while i < len(lines) and lines[i].strip():
            cleaned = _TAG_RE.sub("", lines[i]).strip()
            if cleaned and not cleaned.startswith("WEBVTT"):
                text_parts.append(cleaned)
            i += 1
        text = " ".join(text_parts).strip()
        if text and end > start:
            if cues and cues[-1].text == text:
                cues[-1].end = max(cues[-1].end, end)
            else:
                cues.append(TranscriptCue(start=start, end=end, text=text))
    return cues


def _window_heatmap_score(
    start: float, end: float, heatmap: list[dict] | None
) -> float:
    if not heatmap:
        return 0.0
    peak = 0.0
    for entry in heatmap:
        s = float(entry.get("start_time", 0.0))
        e = float(entry.get("end_time", 0.0))
        if e >= start and s <= end:
            peak = max(peak, float(entry.get("value", 0.0)))
    return round(peak, 3)


def build_candidate_windows(
    video_id: str,
    video_url: str,
    video_title: str,
    channel: str,
    cues: Iterable[TranscriptCue],
    min_sec: float = 18.0,
    max_sec: float = 52.0,
    stride_sec: float = 20.0,
    has_webcam_hint: bool = False,
    heatmap: list[dict] | None = None,
    view_count: int = 0,
) -> List[CandidateSegment]:
    """Group transcript cues into overlapping 15-58s candidate windows."""
    cue_list = list(cues)
    if not cue_list:
        return []

    windows: List[CandidateSegment] = []
    n = len(cue_list)
    idx = 0
    while idx < n:
        win_start = cue_list[idx].start
        texts: List[str] = []
        j = idx
        while j < n and (cue_list[j].end - win_start) <= max_sec:
            texts.append(cue_list[j].text)
            j += 1
        win_end = cue_list[max(idx, j - 1)].end
        dur = win_end - win_start
        if dur >= min_sec:
            end_clamped = round(min(win_end, win_start + 58.0), 2)
            windows.append(
                CandidateSegment(
                    video_id=video_id,
                    video_url=video_url,
                    video_title=video_title,
                    channel=channel,
                    start=round(win_start, 2),
                    end=end_clamped,
                    text=" ".join(texts),
                    has_webcam_hint=has_webcam_hint,
                    replay_heat=_window_heatmap_score(win_start, end_clamped, heatmap),
                    view_count=view_count,
                )
            )
        next_target = win_start + stride_sec
        while idx < n and cue_list[idx].start < next_target:
            idx += 1
        if idx == j and idx < n:
            idx += 1
    return windows


def _processed_video_ids(cfg: AppConfig) -> set[str]:
    history = load_json(cfg.memory_dir / "processed_history.json", {})
    return {
        entry.get("video_id", "")
        for entry in history.get("processed_videos", [])
        if entry.get("video_id")
    }


def fetch_subtitles_only(
    cfg: AppConfig,
    sources: list[str] | None = None,
    playlist_items: int = 2,
) -> List[CandidateSegment]:
    """Fetch metadata + auto-captions via yt-dlp WITHOUT downloading any video."""
    subs_dir = cfg.work_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)
    target_sources = sources if sources is not None else load_sources(cfg)
    seen_ids = _processed_video_ids(cfg)
    candidates: List[CandidateSegment] = []
    js_flags = ["--js-runtimes", "node"] if shutil.which("node") else []

    for source in target_sources:
        base_args = [
            "yt-dlp",
            *js_flags,
            "--skip-download",
            "--write-auto-sub",
            "--write-sub",
            "--sub-langs",
            "en.*,en",
            "--sub-format",
            "vtt/srt",
            "--playlist-end",
            str(playlist_items),
            "--match-filter",
            "!is_live & duration >= 15 & duration <= 28800",
            "--ignore-errors",
            "--no-warnings",
            "--write-info-json",
            "-o",
            str(subs_dir / "%(id)s.%(ext)s"),
        ]
        before_count = len(list(subs_dir.glob("*.info.json")))
        try:
            proc = subprocess.run([*base_args, source], check=False, capture_output=True, text=True, timeout=90)
            after_count = len(list(subs_dir.glob("*.info.json")))
            if after_count == before_count:
                # Self-healing retry with alternate YouTube player clients for datacenter IPs
                proc2 = subprocess.run(
                    [*base_args, "--extractor-args", "youtube:player_client=android_vr,tv", source],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                if len(list(subs_dir.glob("*.info.json"))) == before_count and (proc2.stderr or proc.stderr):
                    err_line = (proc2.stderr or proc.stderr).strip().splitlines()[-1][:200]
                    print(f"[discovery] 0 items for {source}: {err_line}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"[discovery] timeout/error for {source}: {exc}")
            continue

    for info_path in sorted(subs_dir.glob("*.info.json")):
        try:
            meta = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("_type") == "playlist":
            continue
        vid = meta.get("id")
        if not vid or vid in seen_ids:
            continue
        seen_ids.add(vid)
        title = meta.get("title", "")
        desc = (meta.get("description") or "").lower()
        categories = " ".join(str(c) for c in (meta.get("categories") or [])).lower()
        webpage_url = meta.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
        is_twitch_clip = "twitch.tv/" in webpage_url and "/clip/" in webpage_url
        combined = f"{title} {desc} {categories}".lower()
        if not any(
            kw in combined
            for kw in ("gta", "grand theft auto", "leonida", "vice city")
        ):
            continue

        sub_file = None
        for ext in ("en.vtt", "en-orig.vtt", "en-US.vtt", "en.srt", "vtt", "srt"):
            p = subs_dir / f"{vid}.{ext}"
            if p.exists():
                sub_file = p
                break
        if sub_file is None:
            matches = list(subs_dir.glob(f"{vid}*.vtt")) + list(
                subs_dir.glob(f"{vid}*.srt")
            )
            sub_file = matches[0] if matches else None

        channel = meta.get("channel") or meta.get("uploader") or "unknown"
        if is_twitch_clip:
            m_chan = re.search(r"twitch\.tv/([^/]+)/clip/", webpage_url)
            if m_chan:
                channel = m_chan.group(1)

        if sub_file is None:
            dur = float(meta.get("duration") or 0.0)
            if is_twitch_clip and 15.0 <= dur <= 58.0:
                candidates.append(
                    CandidateSegment(
                        video_id=vid,
                        video_url=webpage_url,
                        video_title=title,
                        channel=channel,
                        start=0.0,
                        end=round(dur, 2),
                        text=f"{channel} plays GTA 6: {title}",
                        has_webcam_hint=True,
                        replay_heat=0.0,
                        view_count=int(meta.get("view_count") or 0),
                    )
                )
            continue

        cues = parse_vtt_or_srt(sub_file.read_text(encoding="utf-8", errors="replace"))
        webcam_hint = is_twitch_clip or any(
            kw in title.lower()
            for kw in (
                "react",
                "reaction",
                "stream",
                "vod",
                "live",
                "playthrough",
                "ishowspeed",
                "kai cenat",
                "xqc",
                "caseoh",
            )
        )
        candidates.extend(
            build_candidate_windows(
                video_id=vid,
                video_url=webpage_url,
                video_title=title,
                channel=channel,
                cues=cues,
                has_webcam_hint=webcam_hint,
                heatmap=meta.get("heatmap"),
                view_count=int(meta.get("view_count") or 0),
            )
        )
    return candidates
