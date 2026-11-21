---
name: self-learning-optimizer
description: Audit published clip views/retention, boost winning hook/caption/duration weights in memory/viral_playbook.json, diagnose flops (<500 views at 48h), and log self-healing scraper/MCP fixes in memory/mistakes_and_lessons.json.
---

# Self-Learning Optimizer (Phase 5)

## Mandatory Every-Run Feedback Loop (`src/gta6_clipper/learner.py`)
1. **Metrics Collection:** Query view counts, like ratios, and comments for all clips in `memory/processed_history.json` whose age >= 48h (using YouTube Data API `videos.list` or `yt-dlp --dump-json` public stats / browser check).
2. **Winner Weight Boosting (> +30% vs Channel Average):**
   - Compare each clip's 48h views against `channel_avg_views_48h`.
   - If a hook archetype, caption preset (`gtaclipper_pill` vs `cutawan_beast`), or duration bucket (`15-28s` vs `29-45s` vs `46-58s`) exceeds average by `> 30%`, boost its weight by `+0.15` (capped cleanly and normalized) in `memory/viral_playbook.json` and append an optimization note to `.agent/skills/retention-editor/SKILL.md`.
3. **Flop Post-Mortem (< 500 views after 48h):**
   - Classify root cause (`weak_0_3s_hook`, `wrong_duration_bucket`, `caption_preset_mismatch`, `source_saturation`).
   - Record a concrete prevention rule in `memory/mistakes_and_lessons.json` and reduce the failing parameter's weight.
4. **Self-Healing Tooling & Git Sync:**
   - Catch `yt-dlp`, `ffmpeg`, or MCP HTTP errors; record traceback summary + applied fallback in `memory/mistakes_and_lessons.json`.
   - Commit **only** lightweight `.json` and `.md` updates back to `main`.
