"""Phase 3: Automated ffprobe + frame quality gate."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List


@dataclass
class QualityReport:
    passed: bool
    width: int
    height: int
    duration: float
    audio_present: bool
    av_sync_delta: float
    errors: List[str]

    def to_dict(self) -> dict:
        return asdict(self)


def validate_clip(
    clip_path: Path,
    min_duration: float = 15.0,
    max_duration: float = 58.0,
    expected_width: int = 1080,
    expected_height: int = 1920,
) -> QualityReport:
    """Verify 1080x1920, 15-58s duration, audio sync, and file integrity via ffprobe."""
    errors: List[str] = []
    if not clip_path.is_file() or clip_path.stat().st_size < 1024:
        return QualityReport(
            passed=False,
            width=0,
            height=0,
            duration=0.0,
            audio_present=False,
            av_sync_delta=999.0,
            errors=["Rendered file missing or too small"],
        )

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(clip_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(proc.stdout)
    except Exception as exc:
        return QualityReport(
            passed=False,
            width=0,
            height=0,
            duration=0.0,
            audio_present=False,
            av_sync_delta=999.0,
            errors=[f"ffprobe failed: {exc}"],
        )

    streams = info.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not v_stream:
        errors.append("Missing video stream")
        return QualityReport(
            passed=False,
            width=0,
            height=0,
            duration=0.0,
            audio_present=bool(a_stream),
            av_sync_delta=999.0,
            errors=errors,
        )

    width = int(v_stream.get("width", 0))
    height = int(v_stream.get("height", 0))
    v_dur = float(v_stream.get("duration") or info.get("format", {}).get("duration") or 0.0)
    a_dur = float(a_stream.get("duration") or v_dur) if a_stream else 0.0
    av_delta = round(abs(v_dur - a_dur), 3) if a_stream else 999.0

    if width != expected_width or height != expected_height:
        errors.append(f"Invalid resolution {width}x{height}; expected {expected_width}x{expected_height}")
    if not (min_duration <= v_dur <= max_duration):
        errors.append(f"Duration {v_dur:.2f}s outside [{min_duration}, {max_duration}]s")
    if not a_stream:
        errors.append("Missing audio stream")
    elif av_delta > 0.50:
        errors.append(f"Audio/video drift {av_delta:.2f}s exceeds 0.50s")

    return QualityReport(
        passed=len(errors) == 0,
        width=width,
        height=height,
        duration=round(v_dur, 2),
        audio_present=bool(a_stream),
        av_sync_delta=av_delta,
        errors=errors,
    )
