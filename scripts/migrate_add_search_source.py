"""Backfill search_source = 'wise_link' for all applied_log entries that lack the field.

All historical entries came from wise links (magic links), so 'wise_link' is the correct
retroactive label. Run once after deploying the search_source feature.

Usage:
    python scripts/migrate_add_search_source.py [--dry-run]
"""
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

def migrate_log(log_path: Path, dry_run: bool) -> tuple[int, int]:
    """Returns (total_entries, patched_count)."""
    with open(log_path, encoding="utf-8") as f:
        log = json.load(f)

    if not isinstance(log, list):
        print(f"  ⚠️  Skipping {log_path} — not a list")
        return 0, 0

    patched = 0
    for entry in log:
        if "search_source" not in entry:
            entry["search_source"] = "wise_link"
            patched += 1

    if patched and not dry_run:
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)

    return len(log), patched


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN — no files will be written\n")

    log_paths = list(DATA_DIR.glob("**/applied_log*.json"))
    if not log_paths:
        print("No applied_log files found.")
        return

    total_entries = 0
    total_patched = 0
    for path in sorted(log_paths):
        entries, patched = migrate_log(path, dry_run)
        label = "(would patch)" if dry_run else "patched"
        print(f"  {path.relative_to(BASE_DIR)}: {entries} entries, {patched} {label}")
        total_entries += entries
        total_patched += patched

    action = "would patch" if dry_run else "patched"
    print(f"\nDone: {total_patched}/{total_entries} entries {action} across {len(log_paths)} file(s).")


if __name__ == "__main__":
    main()
