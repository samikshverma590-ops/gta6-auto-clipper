"""Phase 3: Retention editor — Cinema blurred bed + tight OpenCV facecam framing,
FFmpeg-decoded faster-whisper word timestamps (-0.10s lead), Cutawan word grouping,
GTAClipper Poppins ExtraBold yellow-pill ASS captions, 1.06x punch-in, and silence jump-cuts.
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

import numpy as np

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


def detect_facecam_crop(input_path: Path, sample_count: int = 8) -> str | None:
    """Detect tight streamer webcam corner via OpenCV corner-ROI clustering + CLAHE.

    Scans only the 4 outer corner zones (32% width x 50% height) with local CLAHE
    normalization, keeps only the dominant consistent corner cluster via median,
    and returns a tight ~16:9 crop avoiding Twitch chat and game HUD noise.
    """
    try:
        from collections import defaultdict
        import statistics
        import cv2  # type: ignore
    except Exception:
        return None

    if not hasattr(cv2, "CascadeClassifier"):
        return None

    frontal_xml = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    alt2_xml = Path(cv2.data.haarcascades) / "haarcascade_frontalface_alt2.xml"
    profile_xml = Path(cv2.data.haarcascades) / "haarcascade_profileface.xml"
    if not frontal_xml.is_file():
        return None
    frontal_clf = cv2.CascadeClassifier(str(frontal_xml))
    alt2_clf = cv2.CascadeClassifier(str(alt2_xml)) if alt2_xml.is_file() else frontal_clf
    profile_clf = cv2.CascadeClassifier(str(profile_xml)) if profile_xml.is_file() else None

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        return None
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total_frames <= 0:
        cap.release()
        return None

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    buckets: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    step = max(1, total_frames // (sample_count + 1))
    for i in range(1, sample_count + 1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(total_frames - 1, i * step))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        fh, fw = frame.shape[:2]
        min_face = max(32, int(fw * 0.04))
        rois = {
            "tl": (0, 0, int(fw * 0.32), int(fh * 0.50)),
            "tr": (int(fw * 0.68), 0, fw, int(fh * 0.50)),
            "bl": (0, int(fh * 0.50), int(fw * 0.32), fh),
            "br": (int(fw * 0.68), int(fh * 0.50), fw, fh),
        }
        for cname, (rx1, ry1, rx2, ry2) in rois.items():
            gray = clahe.apply(cv2.cvtColor(frame[ry1:ry2, rx1:rx2], cv2.COLOR_BGR2GRAY))
            detectors = ((alt2_clf, 5), (frontal_clf, 5))
            if profile_clf is not None:
                detectors = (*detectors, (profile_clf, 5))
            for clf, mn in detectors:
                faces = clf.detectMultiScale(
                    gray,
                    scaleFactor=1.08,
                    minNeighbors=mn,
                    minSize=(min_face, min_face),
                )
                if len(faces) > 0:
                    x, y, w, h = max(faces, key=lambda b: int(b[2]) * int(b[3]))
                    cx = (rx1 + x + w / 2.0) / max(1.0, fw)
                    cy = (ry1 + y + h / 2.0) / max(1.0, fh)
                    buckets[cname].append((cx, cy, w / max(1.0, fw), h / max(1.0, fh)))
                    break
    cap.release()

    if not buckets:
        return None

    _, pts = max(buckets.items(), key=lambda kv: len(kv[1]))
    if len(pts) < 2:
        return None

    med_x = statistics.median(p[0] for p in pts)
    med_y = statistics.median(p[1] for p in pts)
    inliers = [
        p for p in pts if abs(p[0] - med_x) <= 0.12 and abs(p[1] - med_y) <= 0.12
    ]
    if len(inliers) < 2:
        return None

    cx = statistics.median(p[0] for p in inliers)
    cy = statistics.median(p[1] for p in inliers)
    fw_rel = statistics.median(p[2] for p in inliers)
    fh_rel = statistics.median(p[3] for p in inliers)

    crop_w = max(0.20, min(0.26, fw_rel * 3.1))
    crop_h = max(0.22, min(0.29, fh_rel * 2.4))
    # Snap flush to left/right edge when webcam sits near side border to skip game sky
    if cx < 0.13:
        x0 = 0.0
    elif cx > 0.87:
        x0 = 1.0 - crop_w
    else:
        x0 = max(0.0, min(1.0 - crop_w, cx - crop_w / 2.0))
    y0 = max(0.0, min(1.0 - crop_h, cy - crop_h * 0.45))
    return f"iw*{crop_w:.3f}:ih*{crop_h:.3f}:iw*{x0:.3f}:ih*{y0:.3f}"


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
    line_width_em = max(
        4.0, (aspect_ratio * safe_width) / max(0.01, font_scale) - pop_em
    )

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

    for wt in words:
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
    caption_y_ratio: float | None = None,
) -> Path:
    """Generate ASS subtitle file with top Hook Banner + eye-line word-by-word captions."""
    target = presets_cfg.get("target_resolution", {"width": 1080, "height": 1920})
    w, h = int(target["width"]), int(target["height"])
    preset = presets_cfg.get("caption_presets", {}).get(
        preset_name,
        presets_cfg["caption_presets"]["gtaclipper_pill"],
    )
    guardrails = presets_cfg.get("caption_layout_guardrails", {})

    font_name = preset.get("font_family", "Poppins ExtraBold")
    font_size = max(48, int(round(h * float(preset.get("font_scale", 0.048)))))
    y_ratio = (
        caption_y_ratio
        if caption_y_ratio is not None
        else float(preset.get("position_y", 0.66))
    )
    pos_y = int(round(h * y_ratio))
    margin_v = max(120, h - pos_y)
    uppercase = bool(preset.get("uppercase", True))
    word_box = bool(preset.get("word_box", False))
    box_color = preset.get("word_box_color") or "#FFC400"

    primary_ass = hex_to_ass_bgr(preset.get("text_color", "#FFFFFF"))
    highlight_ass = hex_to_ass_bgr(preset.get("highlight_color", "#FFD400"))
    outline_ass = hex_to_ass_bgr(preset.get("outline_color", "#000000"))
    box_ass = hex_to_ass_bgr(box_color)

    outline_w = float(preset.get("outline_width", 4.5))
    shadow = int(preset.get("shadow", 2))

    # Top Hook Banner positioned inside top blurred cinema margin (y=75px, clear of faces)
    banner_y = 75
    banner_dur = 3.5

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_ass},{primary_ass},{outline_ass},&H90000000,-1,0,0,0,100,100,0,0,1,{outline_w},{shadow},2,60,60,{margin_v},1
Style: Banner,{font_name},{int(font_size * 0.78)},&H00111111&,&H00111111&,&H0000C4FF&,&H0000C4FF&,-1,0,0,0,100,100,1,0,3,16,0,8,50,50,{banner_y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: List[str] = []
    if hook_banner.strip():
        safe_banner = hook_banner.strip().upper().replace("{", "").replace("}", "")
        events.append(
            f"Dialogue: 2,0:00:00.00,{_fmt_ass_time(banner_dur)},Banner,,0,0,0,,{{\\fad(80,180)}}{safe_banner}"
        )

    # Apply 0.10s lead-in so viewers read ahead smoothly without lag
    lead_words = [
        WordTiming(
            word=wt.word,
            start=max(0.0, round(wt.start - 0.10, 3)),
            end=max(0.05, round(wt.end - 0.05, 3)),
        )
        for wt in words
    ]

    groups = group_words_for_captions(
        lead_words, preset, guardrails, aspect_ratio=w / h
    )
    for g_idx, grp in enumerate(groups):
        next_grp_start = (
            groups[g_idx + 1][0].start if g_idx + 1 < len(groups) else None
        )
        for active_idx, active_wt in enumerate(grp):
            t0 = active_wt.start
            t1 = (
                grp[active_idx + 1].start
                if active_idx + 1 < len(grp)
                else max(active_wt.end, active_wt.start + 0.14)
            )
            if next_grp_start is not None and active_idx + 1 == len(grp):
                t1 = min(t1, next_grp_start)
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
                            f"{{\\c{highlight_ass}\\3c{box_ass}\\bord12\\fscx110\\fscy110}}{tok}{{\\r}}"
                        )
                    else:
                        rendered_tokens.append(
                            f"{{\\c{highlight_ass}\\fscx110\\fscy110}}{tok}{{\\r}}"
                        )
                else:
                    rendered_tokens.append(tok)
            line = " ".join(rendered_tokens)
            events.append(
                f"Dialogue: 1,{_fmt_ass_time(t0)},{_fmt_ass_time(t1)},Default,,0,0,0,,{line}"
            )

    safe_path = out_ass_path.with_name(out_ass_path.name.replace("'", ""))
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return safe_path


def _decode_audio_16k_mono(media_path: Path) -> np.ndarray:
    """Decode audio via ffmpeg directly to float32 16kHz mono PCM, bypassing PyAV."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32)


def extract_word_timings(
    media_path: Path,
    fallback_text: str = "",
    clip_duration: float = 0.0,
    allow_fallback: bool = False,
) -> List[WordTiming]:
    """Extract accurate word-level timestamps via FFmpeg-decoded faster-whisper.

    Never emits fake spread-out title words unless allow_fallback=True (for unit tests).
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore

        audio_np = _decode_audio_16k_mono(media_path)
        if audio_np.size > 1600:
            model = WhisperModel("base", device="cpu", compute_type="int8")
            segments, _ = model.transcribe(
                audio_np,
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
                word_timestamps=True,
            )
            words: List[WordTiming] = []
            for seg in segments:
                for w in getattr(seg, "words", []) or []:
                    raw = w.word or ""
                    txt = raw.strip()
                    if not txt:
                        continue
                    if words and not raw.startswith(" "):
                        words[-1].word += txt
                        words[-1].end = float(w.end)
                    else:
                        words.append(
                            WordTiming(
                                word=txt, start=float(w.start), end=float(w.end)
                            )
                        )
            if words:
                return words
    except Exception:
        pass

    if not allow_fallback or not fallback_text.strip():
        return []

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


def _maybe_trim_silences(
    input_path: Path, total_duration: float, tmp_dir: Path
) -> Path:
    """Trim >0.35s dead air segments when remaining duration stays >= 15.0s."""
    if total_duration < 17.5:
        return input_path
    det = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(input_path),
            "-af",
            "silencedetect=noise=-34dB:d=0.35",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    kept = build_silence_trim_segments(
        det.stderr, total_duration, min_silence_sec=0.35
    )
    kept_dur = sum(e - s for s, e in kept)
    if len(kept) <= 1 or kept_dur < 15.0 or (total_duration - kept_dur) < 0.5:
        return input_path

    v_parts = []
    a_parts = []
    concat_inputs = []
    for idx, (s, e) in enumerate(kept[:12]):
        v_parts.append(
            f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{idx}];"
        )
        a_parts.append(
            f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{idx}];"
        )
        concat_inputs.append(f"[v{idx}][a{idx}]")

    trimmed_path = tmp_dir / f"trimmed_{input_path.name}"
    fc = (
        "".join(v_parts + a_parts)
        + "".join(concat_inputs)
        + f"concat=n={len(concat_inputs)}:v=1:a=1[vo][ao]"
    )
    res = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-filter_complex",
            fc,
            "-map",
            "[vo]",
            "-map",
            "[ao]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            str(trimmed_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode == 0 and trimmed_path.is_file():
        return trimmed_path
    return input_path


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
    words_override: Sequence[WordTiming] | None = None,
) -> Path:
    """Render cinema-quality 1080x1920 vertical Short/Reel.

    - Split-Cam (reaction streams): Blurred darkened cinema backdrop + crisp 16:9 tight
      facecam box at y=190 + sharp 9:8 wide gameplay stage at y=810 (trims bottom 4%
      game subtitles) + Top Hook Banner at y=75 + eye-line yellow-pill captions at y=800.
    - Cinema Action (pure gameplay / challenges): Blurred darkened cinema backdrop + sharp
      4:5 action stage (1080x1350) with subtle 1.06x punch-in + captions at y=1390.
    """
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
    raw_duration = max(1.0, float(probe.stdout.strip() or "20.0"))
    working_input = _maybe_trim_silences(input_path, raw_duration, output_path.parent)

    if working_input != input_path:
        probe2 = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(working_input),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = max(1.0, float(probe2.stdout.strip() or str(raw_duration)))
    else:
        duration = raw_duration

    words = (
        list(words_override)
        if words_override is not None
        else extract_word_timings(working_input, transcript_text, duration, allow_fallback=False)
    )

    facecam_crop = detect_facecam_crop(working_input) if split_facecam else None
    # Split-cam places captions at y=0.49 (just below the top facecam at y=190..798)
    # so the speaker's face is never occluded; Cinema Action uses lower center y=0.72.
    caption_y = 0.49 if facecam_crop else 0.72
    safe_ass = build_ass_subtitles(
        words,
        hook_banner,
        preset_name,
        presets_cfg,
        ass_path,
        caption_y_ratio=caption_y,
    )
    ass_escaped = _escape_ffmpeg_path(safe_ass)

    fonts_dir = cfg.repo_root / "assets" / "fonts"
    fonts_arg = (
        f":fontsdir='{_escape_ffmpeg_path(fonts_dir)}'" if fonts_dir.is_dir() else ""
    )

    scale_zoom_w = (
        "trunc(1080*(1+0.06*max(0,sin(PI*t/18))*gt(sin(PI*t/18),0.85))/2)*2"
    )

    if facecam_crop:
        # Cinema blurred backdrop + sharp tight 16:9 facecam (1080x608 at y=190) +
        # sharp wide 9:8 gameplay stage (1080x960 at y=810, trimming bottom 11% burned-in subs)
        filter_complex = (
            "[0:v]split=3[bgsrc][cam][game];"
            "[bgsrc]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
            "boxblur=7:2,scale=1080:1920:flags=bilinear,eq=brightness=-0.16:saturation=1.15,setsar=1[bg];"
            f"[cam]crop={facecam_crop},scale=1080:608:force_original_aspect_ratio=increase,"
            "crop=1080:608,unsharp=5:5:0.6:3:3:0.3,setsar=1[top];"
            "[game]crop=iw:ih*0.89:0:0,crop=min(iw\\,ih*1.125):ih:(iw-min(iw\\,ih*1.125))/2:0,"
            "scale=1080:960,unsharp=5:5:0.6:3:3:0.3,setsar=1[bot];"
            "[bg][top]overlay=0:190[bg1];"
            "[bg1][bot]overlay=0:810,"
            f"ass='{ass_escaped}'{fonts_arg}[v]"
        )
    else:
        # Full Cinema Action layout: blurred cinema backdrop + sharp 4:5 gameplay stage (bottom 11% trimmed)
        filter_complex = (
            "[0:v]split=2[bgsrc][fgsrc];"
            "[bgsrc]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
            "boxblur=7:2,scale=1080:1920:flags=bilinear,eq=brightness=-0.16:saturation=1.15,setsar=1[bg];"
            "[fgsrc]crop=iw:ih*0.89:0:0,scale=1080:1350:force_original_aspect_ratio=increase,crop=1080:1350,"
            f"scale=w='{scale_zoom_w}':h=-2:eval=frame,crop=1080:1350,unsharp=5:5:0.6:3:3:0.3,setsar=1[fg];"
            "[bg][fg]overlay=0:260,"
            f"ass='{ass_escaped}'{fonts_arg}[v]"
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(working_input),
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
        "20",
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
