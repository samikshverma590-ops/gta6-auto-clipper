"""Phase 2: Zero-storage discovery (metadata + subtitles only, never full VODs)."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
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
            # Deduplicate rolling auto-caption lines
            if cues and cues[-1].text == text:
                cues[-1].end = max(cues[-1].end, end)
            else:
                cues.append(TranscriptCue(start=start, end=end, text=text))
    return cues


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
            windows.append(
                CandidateSegment(
                    video_id=video_id,
                    video_url=video_url,
                    video_title=video_title,
                    channel=channel,
                    start=round(win_start, 2),
                    end=round(min(win_end, win_start + 58.0), 2),
                    text=" ".join(texts),
                    has_webcam_hint=has_webcam_hint,
                )
            )
        # Advance by stride_sec
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
    playlist_items: int = 5,
) -> List[CandidateSegment]:
    """Fetch metadata + auto-captions via yt-dlp WITHOUT downloading any video."""
    subs_dir = cfg.work_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)
    target_sources = sources if sources is not None else load_sources(cfg)
    seen_ids = _processed_video_ids(cfg)
    candidates: List[CandidateSegment] = []

    for source in target_sources:
        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-auto-sub",
            "--write-sub",
            "--sub-langs",
            "en.*,en",
            "--sub-format",
            "vtt/srt",
            "--playlist-end",
            str(playlist_items),
            "--ignore-errors",
            "--no-warnings",
            "--write-info-json",
            "-o",
            str(subs_dir / "%(id)s.%(ext)s"),
            source,
        ]
        try:
            subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            continue

    for info_path in sorted(subs_dir.glob("*.info.json")):
        try:
            meta = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        vid = meta.get("id")
        if not vid or vid in seen_ids:
            continue
        seen_ids.add(vid)
        title = meta.get("title", "")
        if "gta" not in title.lower() and "grand theft auto" not in title.lower():
            # Keep GTA 6 focused unless channel is explicitly GTA
            desc = (meta.get("description") or "").lower()
            if "gta" not in desc and "leonida" not in desc:
                continue

        sub_file = None
        for ext in ("en.vtt", "en-US.vtt", "en.srt", "vtt", "srt"):
            p = subs_dir / f"{vid}.{ext}"
            if p.exists():
                sub_file = p
                break
        if sub_file is None:
            matches = list(subs_dir.glob(f"{vid}*.vtt")) + list(
                subs_dir.glob(f"{vid}*.srt")
            )
            sub_file = matches[0] if matches else None

        if sub_file is None:
            continue

        cues = parse_vtt_or_srt(sub_file.read_text(encoding="utf-8", errors="replace"))
        channel = meta.get("channel") or meta.get("uploader") or "unknown"
        webpage_url = meta.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
        webcam_hint = any(
            kw in title.lower()
            for kw in ("react", "reaction", "stream", "vod", "live", "playthrough")
        )
        candidates.extend(
            build_candidate_windows(
                video_id=vid,
                video_url=webpage_url,
                video_title=title,
                channel=channel,
                cues=cues,
                has_webcam_hint=webcam_hint,
            )
        )
    return candidates
