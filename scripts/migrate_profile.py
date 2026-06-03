#!/usr/bin/env python3
"""Migrate flat data/ layout to data/profiles/<name>/.

Usage:
    python scripts/migrate_profile.py --name pm
    python scripts/migrate_profile.py --name pm --source data/ --dry-run

Copies profile-specific files into data/profiles/<name>/.
Does NOT move hh_cookies.json (cookies stay in data/ — shared, account-level).
Does NOT touch .env or any file outside data/.
"""

import argparse
import shutil
import sys
from pathlib import Path

_PROFILE_FILES = [
    "candidate.md",
    "job_preferences.md",
    "tone_of_voice.md",
    "search_urls.txt",
    "filters.json",
    "applied_log.json",
    "llm_cache.json",
    "suggested_queries.txt",
    "resume_facts.md",      # legacy name — copied if present
]

_SKIP_FILES = {"hh_cookies.json"}  # account-level, stays in data/


def migrate(source: Path, dest: Path, dry_run: bool) -> None:
    dest.mkdir(parents=True, exist_ok=True)

    copied, skipped, missing = [], [], []

    for filename in _PROFILE_FILES:
        src = source / filename
        dst = dest / filename
        if not src.exists():
            missing.append(filename)
            continue
        if dry_run:
            print(f"  [dry-run] would copy: {src} → {dst}")
        else:
            shutil.copy2(src, dst)
            print(f"  ✓ {filename}")
        copied.append(filename)

    if missing:
        print(f"\n  — Not found (skipped): {', '.join(missing)}")

    if not dry_run:
        print(f"\n✅ Migrated {len(copied)} file(s) → {dest}")
        print(f"   hh_cookies.json stays in {source} (shared, account-level)")
        print(f"\nVerify: python main.py --profile {dest.name} --dry-run --max 1")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate data/ to data/profiles/<name>/")
    parser.add_argument("--name", required=True,
                        help="Profile name (e.g. pm, support)")
    parser.add_argument("--source", type=Path, default=None,
                        help="Source directory (default: data/ next to this script)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be copied without actually doing it")
    args = parser.parse_args()

    base = Path(__file__).parent.parent
    source = args.source or (base / "data")
    dest = base / "data" / "profiles" / args.name

    if not source.is_dir():
        print(f"❌ Source not found: {source}")
        return 1

    if dest.exists() and any(dest.iterdir()):
        ans = input(f"⚠  {dest} already exists and is not empty. Overwrite? [y/N] ")
        if ans.strip().lower() != "y":
            print("Aborted.")
            return 0

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Migrating {source} → {dest}\n")
    migrate(source, dest, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
