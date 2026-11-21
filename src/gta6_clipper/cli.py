"""Unified CLI for the GTA 6 Auto-Clipper pipeline."""

from __future__ import annotations

import argparse
import json
import sys

from .checkpoint import format_checkpoint_markdown, run_phase0_audit
from .config import load_config
from .discovery import fetch_subtitles_only
from .learner import run_learning_cycle
from .orchestrator import process_scored_clips
from .publisher import publish_and_cleanup, wipe_work_dir
from .scorer import score_and_filter_candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gta6-clipper",
        description="Zero-cost, zero-local-storage autonomous GTA 6 clipping machine",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit", help="Run Phase 0 skills, binary & secret checkpoint")
    sub.add_parser("learn", help="Run Phase 5 self-learning weight optimization")
    run_all = sub.add_parser("run-all", help="Execute full Phases 0-5 cloud pipeline")
    run_all.add_argument("--playlist-items", type=int, default=2)
    run_all.add_argument("--max-clips", type=int, default=3)

    args = parser.parse_args(argv)
    cfg = load_config()

    if args.command == "audit":
        audit = run_phase0_audit(cfg)
        print(format_checkpoint_markdown(audit))
        return 0

    if args.command == "learn":
        summary = run_learning_cycle(cfg)
        print(json.dumps(summary, indent=2))
        return 0

    if args.command == "run-all":
        audit = run_phase0_audit(cfg)
        learn_summary = run_learning_cycle(cfg)
        candidates = fetch_subtitles_only(cfg, playlist_items=args.playlist_items)
        winners = score_and_filter_candidates(candidates, cfg)
        rendered = (
            process_scored_clips(winners, cfg, max_clips=args.max_clips)
            if winners
            else []
        )
        pub_summary = publish_and_cleanup(rendered, cfg)
        wipe_work_dir(cfg.work_dir)
        dashboard = {
            "phase0_missing_skills": audit["missing_skills"],
            "phase0_ready_for_live_publish": audit["ready_for_live_publish"],
            "phase5_learning": learn_summary,
            "phase2_candidates": len(candidates),
            "phase2_winners_85plus": len(winners),
            "phase3_rendered_passed": sum(1 for c in rendered if c.quality_passed),
            "phase4_publish": pub_summary,
        }
        print(json.dumps(dashboard, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
