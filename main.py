#!/usr/bin/env python3
"""
HH Auto — autonomous job application agent.

Usage:
    python main.py                        # normal run
    python main.py --debug                # debug: screenshots + HTML at each step
    python main.py --debug --max 3        # debug, limit to 3 vacancies
    python main.py --search-url "https://hh.ru/search/vacancy?..."  # override search URL
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from config import CONFIG
from logger import Logger
from adapters.hh import HHAdapter
from llm_cover import LLMCover
from hr_matcher import HRMatcher

DEBUG_DIR = Path(os.getenv("DEBUG_DIR", Path(__file__).parent / "debug_screenshots"))


def main():
    parser = argparse.ArgumentParser(description="HH Auto")
    parser.add_argument("--debug", action="store_true",
                        help="Debug mode: screenshots + HTML dumps at each step")
    parser.add_argument("--max", type=int, default=None,
                        help="Max vacancies per session (overrides config)")
    parser.add_argument("--search-url", type=str, default=None,
                        help="Vacancy search URL (overrides config)")
    args = parser.parse_args()

    if args.max:
        CONFIG.max_vacancies_per_session = args.max
    if args.search_url:
        CONFIG.hh_search_url = args.search_url

    debug = args.debug
    session_dir_base = None

    if debug:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir_base = DEBUG_DIR / f"session_{ts}"
        session_dir_base.mkdir(parents=True, exist_ok=True)
        print(f"🐛 DEBUG mode — snapshots in: {session_dir_base}")

    print("🦾 HH Auto")
    print(f"📊 Limits: {CONFIG.max_vacancies_per_session} vacancies, {CONFIG.max_skips} skips")

    logger = Logger()
    adapter = HHAdapter()
    llm_cover = LLMCover()
    hr_matcher = HRMatcher()

    applied_log = logger.load_applied_log()
    initial_log_count = len(applied_log)
    print(f"📄 Loaded applied_log: {initial_log_count} entries")

    processed_count = 0
    skip_count = 0

    try:
        if not adapter.verify():
            print("❌ Pre-flight check failed — fix errors above before starting")
            return 1

        if not adapter.start():
            print("❌ Failed to launch browser")
            return 1

        vacancies = adapter.get_vacancies()
        if not vacancies:
            print("❌ No vacancies found")
            return 1

        print(f"✅ Found {len(vacancies)} vacancies")

        for url, title, index in vacancies:
            if processed_count >= CONFIG.max_vacancies_per_session:
                print(f"⏹ Limit reached: {processed_count} vacancies")
                break

            if skip_count >= CONFIG.max_skips:
                print(f"⏹ Skip limit reached: {skip_count}")
                break

            existing_status = logger.is_processed(url, applied_log)
            if existing_status:
                print(f"⏭ Vacancy #{index} already processed ({existing_status})")
                skip_count += 1
                continue

            print(f"\n{'='*50}")
            print(f"VACANCY #{index}: {title}")
            print(f"URL: {url}")
            logger.log_daily(f"VACANCY #{index}: {title}")
            logger.log_daily(f"URL: {url}")

            vac_debug_dir = None
            if debug and session_dir_base:
                safe_title = "".join(c for c in title[:30] if c.isalnum() or c in " _-").strip()
                vac_debug_dir = session_dir_base / f"{index:02d}_{safe_title}"

            result = adapter.process_vacancy(
                url, title, index, llm_cover, hr_matcher,
                debug=debug, session_dir=vac_debug_dir
            )

            logger.log_result(
                applied_log,
                url=url,
                title=title,
                status=result['status'],
                reason=result['reason'],
                scenario=result.get('scenario', 'unknown'),
                **result.get('details', {})
            )

            processed_count += 1
            logger.log_daily(f"Result: {result['status']} - {result['reason']}")
            print(f"📊 Status: {result['status']} - {result['reason']}")
            print(f"📈 Progress: {processed_count}/{CONFIG.max_vacancies_per_session}")

    except KeyboardInterrupt:
        print("\n⏹ Interrupted by user")

    except Exception as e:
        print(f"\n❌ Critical error: {e}")
        return 1

    finally:
        adapter.close()

        successful, skipped = logger.count_session_results(applied_log, initial_log_count)
        new_entries = applied_log[initial_log_count:]

        print(f"\n{'='*50}")
        print("SESSION SUMMARY")
        print(f"Processed: {processed_count}/{CONFIG.max_vacancies_per_session}")
        print(f"Successful applications: {successful}")
        print(f"Skipped: {skipped}")
        print(f"New log entries: {len(new_entries)}")

        logger.log_session_summary(processed_count, successful, skipped, new_entries)

        print(f"📄 applied_log: {CONFIG.applied_log_path}")
        print(f"📄 daily log: {logger.daily_log_path}")

        if debug and session_dir_base:
            print(f"\n🐛 Debug snapshots: {session_dir_base}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
