#!/usr/bin/env python3
"""
HH Auto — autonomous job application agent.

Usage:
    python main.py                   # normal run (ACTIVE_SITES=hh by default)
    python main.py --debug           # debug: screenshots + HTML at each step
    python main.py --dry-run         # score vacancies, do NOT submit
    python main.py --max 3           # limit to 3 vacancies
"""

import argparse
import os
import sys

from logger import Logger


def load_active_adapters() -> list:
    """Dynamically load adapters listed in ACTIVE_SITES env (default: hh)."""
    active = [s.strip() for s in os.getenv("ACTIVE_SITES", "hh").split(",") if s.strip()]
    adapters = []
    for site in active:
        if site == "hh":
            from adapters.hh import HHAdapter
            adapters.append(HHAdapter())
        else:
            print(f"⚠  Unknown site '{site}' — skipped. Supported: hh")
    return adapters


def main() -> int:
    parser = argparse.ArgumentParser(description="HH Auto — job application agent")
    parser.add_argument("--debug",   action="store_true",
                        help="Debug mode: screenshots + HTML dumps at each step")
    parser.add_argument("--dry-run", action="store_true",
                        help="Score vacancies only — do NOT submit applications")
    parser.add_argument("--max",     type=int, default=None,
                        help="Max vacancies per session (overrides MAX_VACANCIES in .env)")
    args = parser.parse_args()

    from config import CONFIG
    if args.max:
        CONFIG.max_vacancies_per_session = args.max

    dry_run = args.dry_run
    debug   = args.debug

    print("🦾 HH Auto")
    if dry_run:
        print("🔍 DRY-RUN — scoring only, no applications submitted")
    if debug:
        print("🐛 DEBUG — verbose snapshots enabled")

    logger  = Logger()
    adapters = load_active_adapters()

    if not adapters:
        print("❌ No adapters configured. Set ACTIVE_SITES=hh in .env")
        return 1

    all_results = []

    for adapter in adapters:
        print(f"\n── [{adapter.name()}] ──────────────────────────────────")

        if not adapter.verify():
            print(f"❌ [{adapter.name()}] Pre-flight failed — skipping")
            continue

        if not adapter.start():
            print(f"❌ [{adapter.name()}] Failed to start — skipping")
            continue

        try:
            results = adapter.run(logger, dry_run=dry_run, debug=debug)
            all_results.extend(results)
        except KeyboardInterrupt:
            print(f"\n⏹  [{adapter.name()}] Interrupted")
            break
        except Exception as e:
            print(f"\n❌ [{adapter.name()}] Critical error: {e}")
        finally:
            adapter.close()

    # ── Session summary ───────────────────────────────────────────────────────
    successful = sum(1 for e in all_results if e.get("status", "").startswith("applied"))
    skipped    = sum(1 for e in all_results if e.get("status", "").startswith("skipped"))
    unverified = sum(1 for e in all_results if e.get("status") == "applied_unverified")
    scores     = [e.get("match_score") for e in all_results
                  if isinstance(e.get("match_score"), (int, float))]
    avg_score  = round(sum(scores) / len(scores)) if scores else None

    skip_breakdown: dict = {}
    for e in all_results:
        st = e.get("status", "")
        if st.startswith("skipped"):
            skip_breakdown[st] = skip_breakdown.get(st, 0) + 1

    print(f"\n{'─'*52}")
    print("DRY-RUN COMPLETE" if dry_run else "SESSION COMPLETE")
    print(f"  Applied:     {successful}" + (f" ({unverified} unverified)" if unverified else ""))
    print(f"  Skipped:     {skipped}" + (f"  {skip_breakdown}" if skip_breakdown else ""))
    print(f"  Avg score:   {avg_score if avg_score is not None else 'n/a'}")
    print(f"  Log entries: {len(all_results)}")
    if not dry_run:
        from config import CONFIG as _cfg
        print(f"  Log file:    {_cfg.applied_log_path}")
    print(f"{'─'*52}")

    if unverified:
        print(f"⚠️  {unverified} applied_unverified — check debug_screenshots/auto_* for DOM snapshots")

    logger.log_session_summary(len(all_results), successful, skipped, all_results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
