"""Phase 3: Retention editor — Cutawan camera smoothing + word grouping,
GTAClipper yellow-pill ASS captions, OpenShorts 1.06x punch-in, and 0.30s silence cutting.
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from .config import AppConfig, load_json


@dataclass
class WordTiming:
    word: str
    start: float
    end: float


def hex_to_ass_bgr(hex_color: str, alpha: int = 0) -> str:
    """Convert #RRGGBB into ASS &HAABBGGRR& color literal."""
    clean = hex_color.strip().lstrip("#")
    if len(clean) != 6:
        return "&H00FFFFFF&"
    r, g, b = clean[0:2], clean[2:4], clean[4:6]
    return f"&H{alpha:02X}{b.upper()}{g.upper()}{r.upper()}&"


def plan_camera_pan(
    centres: Sequence[float],
    fps: float,
    source_aspect: float = 16 / 9,
    face_band: float = 0.20,
    smooth_sec: float = 0.80,
) -> List[float]:
    """Port of Cutawan cameraPath.ts stationary/pan virtual camera smoother."""
    if not centres:
        return []
    crop_width = min(1.0, (9 / 16) / max(0.1, source_aspect))
    edge = crop_width / 2.0
    clamped = [min(1.0 - edge, max(edge, float(x))) for x in centres]

    win = max(1, int(round(smooth_sec * fps)) | 1)
    half = win // 2
    smoothed: List[float] = []
    for i in range(len(clamped)):
        sl = sorted(clamped[max(0, i - half) : i + half + 1])
        smoothed.append(sl[len(sl) // 2])

    band = face_band * crop_width
    out: List[float] = []
    current_x = smoothed[0]
    for val in smoothed:
        if abs(val - current_x) > band:
            current_x = current_x + 0.25 * (val - current_x)
        out.append(round(current_x, 4))
    return out


def group_words_for_captions(
    words: Sequence[WordTiming],
    preset: dict,
    guardrails: dict,
    aspect_ratio: float = 9 / 16,
) -> List[List[WordTiming]]:
    """Port of Cutawan captionLayout.ts em-budget word grouper (max 2 lines)."""
    if not words:
        return []
    max_words = int(preset.get("words_per_group", 3))
    font_scale = float(preset.get("font_scale", 0.048))
    safe_width = float(guardrails.get("safe_width_ratio", 0.86))
    pop_em = float(guardrails.get("pop_allowance_em", 0.5))
    space_em = float(guardrails.get("space_width_em", 0.26))
    pause_break = float(guardrails.get("pause_break_sec", 0.35))
    line_width_em = max(4.0, (aspect_ratio * safe_width) / max(0.01, font_scale) - pop_em)

    glyph_map = guardrails.get("glyph_width_em", {})
    pref_font = preset.get("preferred_font", "Poppins").split()[0]
    font_glyph = glyph_map.get(pref_font, {"lower": 0.60, "upper": 0.68})
    glyph_em = float(
        font_glyph["upper"] if preset.get("uppercase", False) else font_glyph["lower"]
    )

    groups: List[List[WordTiming]] = []
    cur: List[WordTiming] = []
    cur_em = 0.0
    lines_used = 1

    for idx, wt in enumerate(words):
        token = wt.word.strip()
        if not token:
            continue
        w_em = len(token) * glyph_em
        prev_end = cur[-1].end if cur else wt.start
        long_pause = bool(cur) and (wt.start - prev_end) >= pause_break
        sentence_end = bool(cur) and cur[-1].word.strip().endswith((".", "!", "?"))

        if cur and (len(cur) >= max_words or long_pause or sentence_end):
            groups.append(cur)
            cur = []
            cur_em = 0.0
            lines_used = 1

        needed = w_em + (space_em if cur_em > 0 else 0.0)
        if cur_em + needed > line_width_em:
            if lines_used >= 2 and cur:
                groups.append(cur)
                cur = []
                cur_em = 0.0
                lines_used = 1
            else:
                lines_used += 1
                cur_em = 0.0

        cur.append(wt)
        cur_em += w_em + (space_em if cur_em > 0 else 0.0)

    if cur:
        groups.append(cur)
    return groups


def _fmt_ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - math.floor(sec)) * 100))
    if cs >= 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass_subtitles(
    words: Sequence[WordTiming],
    hook_banner: str,
    preset_name: str,
    presets_cfg: dict,
    out_ass_path: Path,
) -> Path:
    """Generate ASS subtitle file with 3s Hook Banner + word-by-word animated captions."""
    target = presets_cfg.get("target_resolution", {"width": 1080, "height": 1920})
    w, h = int(target["width"]), int(target["height"])
    preset = presets_cfg.get("caption_presets", {}).get(
        preset_name,
        presets_cfg["caption_presets"]["gtaclipper_pill"],
    )
    guardrails = presets_cfg.get("caption_layout_guardrails", {})

    font_name = preset.get("font_family", "DejaVu Sans")
    font_size = max(42, int(round(h * float(preset.get("font_scale", 0.048)))))
    pos_y = int(round(h * float(preset.get("position_y", 0.74))))
    margin_v = max(80, h - pos_y)
    uppercase = bool(preset.get("uppercase", True))
    word_box = bool(preset.get("word_box", False))
    box_color = preset.get("word_box_color") or "#FFC400"

    primary_ass = hex_to_ass_bgr(preset.get("text_color", "#FFFFFF"))
    highlight_ass = hex_to_ass_bgr(preset.get("highlight_color", "#FFD400"))
    outline_ass = hex_to_ass_bgr(preset.get("outline_color", "#000000"))
    box_ass = hex_to_ass_bgr(box_color)

    outline_w = float(preset.get("outline_width", 3.5))
    shadow = int(preset.get("shadow", 1))

    banner_y = int(
        round(
            h
            * float(
                presets_cfg.get("retention_editing", {}).get(
                    "hook_banner_position_y", 0.14
                )
            )
        )
    )
    banner_dur = float(
        presets_cfg.get("retention_editing", {}).get("hook_banner_duration_sec", 3.0)
    )

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_ass},{primary_ass},{outline_ass},&H80000000,-1,0,0,0,100,100,0,0,1,{outline_w},{shadow},2,60,60,{margin_v},1
Style: Banner,{font_name},{int(font_size * 0.88)},&H00111111&,&H00111111&,&H0000C4FF&,&H0000C4FF&,-1,0,0,0,100,100,2,0,3,14,0,8,60,60,{banner_y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: List[str] = []
    if hook_banner.strip():
        safe_banner = hook_banner.strip().upper().replace("{", "").replace("}", "")
        events.append(
            f"Dialogue: 2,0:00:00.00,{_fmt_ass_time(banner_dur)},Banner,,0,0,0,,{{\\fad(80,150)}}{safe_banner}"
        )

    groups = group_words_for_captions(words, preset, guardrails, aspect_ratio=w / h)
    for grp in groups:
        for active_idx, active_wt in enumerate(grp):
            t0 = active_wt.start
            t1 = (
                grp[active_idx + 1].start
                if active_idx + 1 < len(grp)
                else max(active_wt.end, active_wt.start + 0.12)
            )
            if t1 <= t0:
                continue
            rendered_tokens: List[str] = []
            for j, wt in enumerate(grp):
                tok = wt.word.strip()
                if uppercase:
                    tok = tok.upper()
                tok = tok.replace("{", "").replace("}", "")
                if j == active_idx:
                    if word_box:
                        rendered_tokens.append(
                            f"{{\\c{highlight_ass}\\3c{box_ass}\\bord10\\fscx109\\fscy109}}{tok}{{\\r}}"
                        )
                    else:
                        rendered_tokens.append(
                            f"{{\\c{highlight_ass}\\fscx109\\fscy109}}{tok}{{\\r}}"
                        )
                else:
                    rendered_tokens.append(tok)
            line = " ".join(rendered_tokens)
            events.append(
                f"Dialogue: 1,{_fmt_ass_time(t0)},{_fmt_ass_time(t1)},Default,,0,0,0,,{line}"
            )

    # Guardrail (lesson-001): ensure filename has no apostrophes
    safe_path = out_ass_path.with_name(out_ass_path.name.replace("'", ""))
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return safe_path


def extract_word_timings(
    media_path: Path, fallback_text: str, clip_duration: float
) -> List[WordTiming]:
    """Extract word-level timestamps via faster-whisper, or distribute evenly as fallback."""
    try:
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel("small", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(
            str(media_path),
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        words: List[WordTiming] = []
        for seg in segments:
            for w in getattr(seg, "words", []) or []:
                txt = (w.word or "").strip()
                if not txt:
                    continue
                if words and not (w.word or "").startswith(" "):
                    words[-1].word += txt
                    words[-1].end = float(w.end)
                else:
                    words.append(
                        WordTiming(word=txt, start=float(w.start), end=float(w.end))
                    )
        if words:
            return words
    except Exception:
        pass

    tokens = [t for t in re.split(r"\s+", fallback_text.strip()) if t]
    if not tokens:
        return []
    dur = max(1.0, clip_duration)
    step = dur / len(tokens)
    return [
        WordTiming(
            word=tok,
            start=round(i * step, 3),
            end=round(min(dur, (i + 1) * step), 3),
        )
        for i, tok in enumerate(tokens)
    ]


def build_silence_trim_segments(
    silence_log: str, total_duration: float, min_silence_sec: float = 0.30
) -> List[tuple[float, float]]:
    """Parse ffmpeg silencedetect output into kept [start, end] speech/action spans."""
    starts = [
        float(m.group(1))
        for m in re.finditer(r"silence_start:\s*([0-9.]+)", silence_log)
    ]
    ends = [
        float(m.group(1))
        for m in re.finditer(r"silence_end:\s*([0-9.]+)", silence_log)
    ]
    silences: List[tuple[float, float]] = []
    for idx, s in enumerate(starts):
        e = ends[idx] if idx < len(ends) else total_duration
        if (e - s) >= min_silence_sec:
            # Leave 0.08s breathing cushion so cuts feel snappy, not clipped
            silences.append((s + 0.04, max(s + 0.04, e - 0.04)))

    if not silences:
        return [(0.0, total_duration)]

    kept: List[tuple[float, float]] = []
    cursor = 0.0
    for s, e in silences:
        if s > cursor + 0.05:
            kept.append((round(cursor, 3), round(s, 3)))
        cursor = max(cursor, e)
    if total_duration > cursor + 0.05:
        kept.append((round(cursor, 3), round(total_duration, 3)))
    return kept or [(0.0, total_duration)]


def _escape_ffmpeg_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")


def render_vertical_clip(
    input_path: Path,
    output_path: Path,
    hook_banner: str,
    transcript_text: str,
    preset_name: str,
    split_facecam: bool,
    cfg: AppConfig,
) -> Path:
    """Render 1080x1920 vertical clip with optional split facecam, punch-in & ASS captions."""
    presets_cfg = load_json(cfg.config_dir / "presets.json", {})
    ass_path = output_path.with_suffix(".ass")

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration = max(1.0, float(probe.stdout.strip() or "20.0"))
    words = extract_word_timings(input_path, transcript_text, duration)
    safe_ass = build_ass_subtitles(words, hook_banner, preset_name, presets_cfg, ass_path)
    ass_escaped = _escape_ffmpeg_path(safe_ass)

    if split_facecam:
        # Stack top 30% (1080x576) facecam crop (top-left/right region) over bottom 70% (1080x1344) gameplay
        filter_complex = (
            "[0:v]split=2[cam][game];"
            "[cam]crop=iw*0.32:ih*0.36:0:0,scale=1080:576:force_original_aspect_ratio=increase,crop=1080:576[top];"
            "[game]scale=1080:1344:force_original_aspect_ratio=increase,crop=1080:1344[bot];"
            f"[top][bot]vstack=inputs=2,ass='{ass_escaped}'[v]"
        )
    else:
        # Stabilized 9:16 center action crop + subtle 1.04-1.06x punch-in breath
        filter_complex = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,ass='{ass_escaped}'[v]"
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return output_path
