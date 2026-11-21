"""Phase 2: 100-point viral moment scoring against memory/viral_playbook.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import List

import httpx

from .config import AppConfig, load_json
from .discovery import CandidateSegment


@dataclass
class ScoredClip:
    video_id: str
    video_url: str
    video_title: str
    channel: str
    start: float
    end: float
    duration: float
    text: str
    score: int
    hook_impact: int
    escalation_arc: int
    sensory_spike: int
    cold_viewer_clarity: int
    hook_archetype: str
    hook_banner: str
    title_idea: str
    has_webcam_hint: bool
    caption_preset: str
    policy_safe: bool

    def to_dict(self) -> dict:
        return asdict(self)


_SPOILER_OR_POLICY_FLAGS = (
    "ending cutscene",
    "final mission ending",
    "full cutscene no commentary",
    "leaked dev build",
)

_HIGH_INTENSITY_WORDS = {
    "what",
    "insane",
    "crazy",
    "secret",
    "hidden",
    "impossible",
    "detail",
    "wait",
    "bro",
    "look",
    "actually",
    "no way",
    "police",
    "arrested",
    "explosion",
    "snake",
    "crab",
    "mandy",
    "dexter",
    "jason",
    "lucia",
    "leonida",
    "vice city",
    "rockstar",
}


def _policy_safe(seg: CandidateSegment) -> bool:
    combined = f"{seg.video_title} {seg.text}".lower()
    return not any(flag in combined for flag in _SPOILER_OR_POLICY_FLAGS)


def _pick_hook_archetype(text_lower: str, playbook: dict) -> tuple[str, str, float]:
    best_id = "npc_ai_shock"
    best_banner = "WAIT FOR IT"
    best_weight = 1.0
    best_hits = -1
    for arch in playbook.get("hook_archetypes", []):
        hits = sum(1 for kw in arch.get("keywords", []) if kw in text_lower)
        if hits > best_hits:
            best_hits = hits
            best_id = arch["id"]
            banners = arch.get("banner_examples", ["WAIT FOR IT"])
            best_banner = banners[0]
            best_weight = float(arch.get("weight", 1.0))
    return best_id, best_banner, best_weight


def score_heuristic(seg: CandidateSegment, playbook: dict) -> ScoredClip:
    """Deterministic playbook-weighted 0-100 viral scorer."""
    text_lower = f"{seg.video_title} {seg.text}".lower()
    words = re.findall(r"[a-zA-Z0-9']+", seg.text)
    first_10 = " ".join(words[:10]).lower()

    arch_id, banner, arch_weight = _pick_hook_archetype(text_lower, playbook)

    # 1. 0-3s Hook Impact (0..35)
    hook = 18
    if any(w in first_10 for w in ("what", "wait", "look", "bro", "rockstar", "secret", "no", "why", "how")):
        hook += 9
    if "!" in seg.text[:80] or "?" in seg.text[:80]:
        hook += 5
    if any(bad in first_10 for bad in ("hey guys", "welcome back", "subscribe", "in today's video")):
        hook -= 12
    hook = max(0, min(35, int(round(hook * min(1.2, arch_weight)))))

    # 2. Escalation & Pacing Arc (0..25)
    dur = seg.duration
    word_rate = len(words) / max(1.0, dur)
    escalation = 14
    if 18.0 <= dur <= 48.0:
        escalation += 6
    if 2.0 <= word_rate <= 4.2:
        escalation += 5
    escalation = max(0, min(25, escalation))

    # 3. Sensory / Emotional Spike (0..20)
    keyword_hits = sum(1 for kw in _HIGH_INTENSITY_WORDS if kw in text_lower)
    sensory = min(20, 8 + keyword_hits * 3)

    # 4. Cold-Viewer Clarity (0..20)
    clarity = 14
    if any(e in text_lower for e in ("gta 6", "gta vi", "vice city", "leonida", "jason", "lucia", "rockstar", "npc")):
        clarity += 6
    clarity = max(0, min(20, clarity))

    total = max(0, min(100, hook + escalation + sensory + clarity))

    # Select highest-weighted caption preset from playbook
    preset_weights = (
        playbook.get("pacing_and_duration", {}).get("caption_preset_weights", {})
    )
    caption_preset = (
        max(preset_weights.items(), key=lambda kv: kv[1])[0]
        if preset_weights
        else "gtaclipper_pill"
    )

    clean_title = re.sub(r"\s+", " ", seg.video_title).strip()[:65]
    return ScoredClip(
        video_id=seg.video_id,
        video_url=seg.video_url,
        video_title=seg.video_title,
        channel=seg.channel,
        start=seg.start,
        end=seg.end,
        duration=seg.duration,
        text=seg.text,
        score=total,
        hook_impact=hook,
        escalation_arc=escalation,
        sensory_spike=sensory,
        cold_viewer_clarity=clarity,
        hook_archetype=arch_id,
        hook_banner=banner,
        title_idea=f"{banner.title()} — {clean_title}"[:90],
        has_webcam_hint=seg.has_webcam_hint,
        caption_preset=caption_preset,
        policy_safe=_policy_safe(seg),
    )


def _score_with_gemini(
    seg: CandidateSegment, playbook: dict, cfg: AppConfig
) -> ScoredClip | None:
    if not cfg.gemini_api_key:
        return None
    prompt = (
        "Score this GTA 6 15-58s video transcript segment from 0 to 100 using this rubric:\n"
        "- hook_impact (0-35): immediate action or curiosity hook in first 3s; penalize slow intros.\n"
        "- escalation_arc (0-25): rising tension, comedy, or pacing.\n"
        "- sensory_spike (0-20): shock, laughter, rare GTA 6 detail/Easter egg, or chaos.\n"
        "- cold_viewer_clarity (0-20): understandable with zero prior context.\n"
        "Return strict JSON with keys: hook_impact, escalation_arc, sensory_spike, "
        "cold_viewer_clarity, hook_archetype, hook_banner (3 uppercase words max), title_idea (<=80 chars), policy_safe (bool).\n"
        f"Playbook archetypes: {[a['id'] for a in playbook.get('hook_archetypes', [])]}\n"
        f"Video title: {seg.video_title}\n"
        f"Duration: {seg.duration}s\n"
        f"Transcript: {seg.text[:1500]}"
    )
    models_to_try = list(
        dict.fromkeys(
            [
                cfg.gemini_model,
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash-lite",
                "gemini-3.8-flash",
            ]
        )
    )
    for model_name in models_to_try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={cfg.gemini_api_key}"
        )
        try:
            resp = httpx.post(
                url,
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(raw_text)
            hook = max(0, min(35, int(data["hook_impact"])))
            esc = max(0, min(25, int(data["escalation_arc"])))
            sens = max(0, min(20, int(data["sensory_spike"])))
            clar = max(0, min(20, int(data["cold_viewer_clarity"])))
            total = hook + esc + sens + clar
            preset_weights = (
                playbook.get("pacing_and_duration", {}).get("caption_preset_weights", {})
            )
            caption_preset = (
                max(preset_weights.items(), key=lambda kv: kv[1])[0]
                if preset_weights
                else "gtaclipper_pill"
            )
            banner_words = str(data.get("hook_banner", "WAIT FOR IT")).upper().split()[:3]
            return ScoredClip(
                video_id=seg.video_id,
                video_url=seg.video_url,
                video_title=seg.video_title,
                channel=seg.channel,
                start=seg.start,
                end=seg.end,
                duration=seg.duration,
                text=seg.text,
                score=total,
                hook_impact=hook,
                escalation_arc=esc,
                sensory_spike=sens,
                cold_viewer_clarity=clar,
                hook_archetype=str(data.get("hook_archetype", "npc_ai_shock")),
                hook_banner=" ".join(banner_words) or "WAIT FOR IT",
                title_idea=str(data.get("title_idea", seg.video_title))[:90],
                has_webcam_hint=seg.has_webcam_hint,
                caption_preset=caption_preset,
                policy_safe=bool(data.get("policy_safe", True)) and _policy_safe(seg),
            )
        except Exception:
            continue
    return None


def score_and_filter_candidates(
    candidates: List[CandidateSegment],
    cfg: AppConfig,
    threshold: int | None = None,
    max_per_source: int = 3,
) -> List[ScoredClip]:
    """Score all candidate windows, filter < threshold (85) and keep top 3 per source."""
    playbook = load_json(cfg.memory_dir / "viral_playbook.json", {})
    min_score = cfg.min_viral_score if threshold is None else threshold
    by_video: dict[str, List[ScoredClip]] = {}

    for seg in candidates:
        if not (15.0 <= seg.duration <= 58.0):
            continue
        scored = _score_with_gemini(seg, playbook, cfg) or score_heuristic(seg, playbook)
        if not scored.policy_safe or scored.score < min_score:
            continue
        by_video.setdefault(scored.video_id, []).append(scored)

    winners: List[ScoredClip] = []
    for vid, items in by_video.items():
        items.sort(key=lambda c: c.score, reverse=True)
        # De-overlap selected timestamps within the same video
        picked: List[ScoredClip] = []
        for item in items:
            if any(not (item.end <= p.start or item.start >= p.end) for p in picked):
                continue
            picked.append(item)
            if len(picked) >= max_per_source:
                break
        winners.extend(picked)

    winners.sort(key=lambda c: c.score, reverse=True)
    return winners
