"""Phase 2: 100-point viral moment scoring & metadata optimization."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
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
    youtube_title: str = ""
    tiktok_caption: str = ""
    instagram_caption: str = ""
    hashtags: List[str] = field(default_factory=list)
    pinned_question: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_SPOILER_OR_POLICY_FLAGS = (
    "ending cutscene",
    "final mission ending",
    "full cutscene no commentary",
    "leaked dev build",
    "sad ending",
    "ending explained",
    "final mission",
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
    """Deterministic playbook-weighted 0-100 viral scorer with YouTube heatmap boost."""
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
    is_twitch_clip = "twitch.tv/" in seg.video_url and "/clip/" in seg.video_url
    word_rate = len(words) / max(1.0, dur)
    escalation = 14
    if 18.0 <= dur <= 48.0:
        escalation += 6
    if 2.0 <= word_rate <= 4.2 or (is_twitch_clip and seg.view_count >= 15_000):
        escalation += 5
    escalation = max(0, min(25, escalation))

    # 3. Sensory / Emotional Spike (0..20) boosted by YouTube Most-Replayed heatmap or Twitch clip views
    keyword_hits = sum(1 for kw in _HIGH_INTENSITY_WORDS if kw in text_lower)
    heat_bonus = int(round(seg.replay_heat * 6))
    twitch_bonus = 6 if (is_twitch_clip and seg.view_count >= 25_000) else (4 if is_twitch_clip and seg.view_count >= 10_000 else 0)
    sensory = min(20, 8 + keyword_hits * 3 + heat_bonus + twitch_bonus)

    # 4. Cold-Viewer Clarity (0..20)
    clarity = 14
    if any(e in text_lower for e in ("gta 6", "gta vi", "vice city", "leonida", "jason", "lucia", "rockstar", "npc")):
        clarity += 6
    clarity = max(0, min(20, clarity))

    total = max(0, min(100, hook + escalation + sensory + clarity))

    preset_weights = (
        playbook.get("pacing_and_duration", {}).get("caption_preset_weights", {})
    )
    caption_preset = (
        max(preset_weights.items(), key=lambda kv: kv[1])[0]
        if preset_weights
        else "gtaclipper_pill"
    )

    clean_title = re.sub(r"\s+", " ", seg.video_title).strip()[:65]
    creator_tag = "#" + re.sub(r"[^a-zA-Z0-9]", "", seg.channel)
    tags = ["#Shorts", "#GTA6", "#GTAVI", "#ViceCity", "#Leonida", "#RockstarGames"]
    if len(creator_tag) > 2:
        tags.append(creator_tag)

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
        youtube_title=f"{banner.title()} in GTA 6! ({seg.channel}) #Shorts"[:100],
        hashtags=tags[:7],
        pinned_question="Did you catch this in Vice City yet? Rate 1-10 👇",
    )


def _score_with_gemini(
    seg: CandidateSegment, playbook: dict, cfg: AppConfig
) -> ScoredClip | None:
    if not cfg.gemini_api_key:
        return None
    is_twitch_clip = "twitch.tv/" in seg.video_url and "/clip/" in seg.video_url
    source_note = (
        "This is a human-curated top 7-day Twitch viral highlight (full audio will be word-by-word captioned with faster-whisper)."
        if is_twitch_clip
        else "Remember: auto-captions omit facial expressions, screaming volume, and insane Vice City visuals — infer energy and curiosity from the words + view count."
    )
    prompt = (
        "You are a viral GTA 6 Shorts director evaluating a candidate clip from a live gaming/reaction stream.\n"
        f"{source_note}\n"
        "Score 0-100 using these calibrated anchors:\n"
        "  - 85-95: Strong standalone chaos, hilarious streamer reaction, shocking GTA 6 secret/NPC detail, or immediate curiosity hook (high-view Twitch GTA 6 highlights with strong titles belong here).\n"
        "  - 65-84: Good moment but slow build-up, generic title, or needs prior context.\n"
        "  - <65: Boring filler, menu/loading talk, or slow intro ('hey guys subscribe').\n"
        "Sub-scores:\n"
        "- hook_impact (0-35): immediate action, shock, or bold claim in first 3s.\n"
        "- escalation_arc (0-25): rising tension, comedy, or payoff.\n"
        "- sensory_spike (0-20): shock, laughter, rare GTA 6 detail/Easter egg, or chaos.\n"
        "- cold_viewer_clarity (0-20): understandable with zero prior context.\n"
        "Return STRICT JSON with keys:\n"
        "  hook_impact, escalation_arc, sensory_spike, cold_viewer_clarity, hook_archetype, "
        "hook_banner (3 uppercase words max), title_idea (<=80 chars), youtube_title (<=95 chars ending with #Shorts), "
        "tiktok_caption (<=150 chars punchy hook), instagram_caption (<=220 chars), "
        "hashtags (array of 6 high-reach tags starting with #), pinned_question (1 short comment-bait question), "
        "policy_safe (bool; false ONLY if it is an unedited full cutscene or ending spoiler).\n"
        f"Playbook archetypes: {[a['id'] for a in playbook.get('hook_archetypes', [])]}\n"
        f"Creator/Channel: {seg.channel} | Clip Views: {seg.view_count:,}\n"
        f"Video title: {seg.video_title}\n"
        f"Duration: {seg.duration}s | YouTube Most-Replayed Heat: {seg.replay_heat}\n"
        f"Transcript/Context: {seg.text[:1500]}"
    )
    models_to_try = list(
        dict.fromkeys(
            [
                cfg.gemini_model,
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash-lite",
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
                timeout=6.0,
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
            raw_tags = data.get("hashtags") or ["#Shorts", "#GTA6", "#GTAVI", "#ViceCity", "#Leonida"]
            clean_tags = [t if str(t).startswith("#") else f"#{t}" for t in raw_tags][:7]
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
                youtube_title=str(data.get("youtube_title", ""))[:100],
                tiktok_caption=str(data.get("tiktok_caption", ""))[:220],
                instagram_caption=str(data.get("instagram_caption", ""))[:400],
                hashtags=clean_tags,
                pinned_question=str(data.get("pinned_question", ""))[:160],
            )
        except Exception:
            continue
    return None


def score_and_filter_candidates(
    candidates: List[CandidateSegment],
    cfg: AppConfig,
    threshold: int | None = None,
    max_per_source: int = 3,
    max_llm_calls: int = 25,
) -> List[ScoredClip]:
    """Pre-rank by heuristic/heatmap/views, round-robin top diverse videos via Gemini, filter < 85."""
    playbook = load_json(cfg.memory_dir / "viral_playbook.json", {})
    min_score = cfg.min_viral_score if threshold is None else threshold

    grouped_raw: dict[str, List[tuple[int, CandidateSegment]]] = {}
    for seg in candidates:
        if not (15.0 <= seg.duration <= 58.0) or not _policy_safe(seg):
            continue
        pre = score_heuristic(seg, playbook)
        view_bonus = 10 if seg.view_count >= 200_000 else (6 if seg.view_count >= 50_000 else 0)
        grouped_raw.setdefault(seg.video_id, []).append(
            (pre.score + int(seg.replay_heat * 10) + view_bonus, seg)
        )

    # Round-robin across videos (1st best window of each video first, then 2nd best)
    best_per_vid: List[tuple[int, CandidateSegment]] = []
    second_per_vid: List[tuple[int, CandidateSegment]] = []
    for vid, scored_pairs in grouped_raw.items():
        scored_pairs.sort(key=lambda pair: pair[0], reverse=True)
        picked_for_vid: List[CandidateSegment] = []
        for pre_score, seg in scored_pairs:
            if any(not (seg.end <= s.start or seg.start >= s.end) for s in picked_for_vid):
                continue
            picked_for_vid.append(seg)
            if len(picked_for_vid) == 1:
                best_per_vid.append((pre_score, seg))
            else:
                second_per_vid.append((pre_score, seg))
                break

    best_per_vid.sort(key=lambda pair: pair[0], reverse=True)
    second_per_vid.sort(key=lambda pair: pair[0], reverse=True)
    shortlist = [seg for _, seg in (best_per_vid + second_per_vid)[:max_llm_calls]]

    by_video_winners: dict[str, List[ScoredClip]] = {}
    consecutive_llm_failures = 0
    for seg in shortlist:
        scored = None
        if cfg.gemini_api_key and consecutive_llm_failures < 2:
            scored = _score_with_gemini(seg, playbook, cfg)
            if scored is None:
                consecutive_llm_failures += 1
            else:
                consecutive_llm_failures = 0
        if scored is None:
            scored = score_heuristic(seg, playbook)
        if not scored.policy_safe or scored.score < min_score:
            continue
        cur = by_video_winners.setdefault(scored.video_id, [])
        if len(cur) < max_per_source:
            cur.append(scored)

    winners = [c for items in by_video_winners.values() for c in items]
    winners.sort(key=lambda c: c.score, reverse=True)
    return winners
