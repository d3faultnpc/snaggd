"""
Regression tests for onboarding/profile_guard.py — the rule that a profile save
may add or change, but may not empty.

What it pins down (2026-08-11): a GUI wizard save replaced a populated candidate.md holding a full work history with an empty placeholder skeleton, and created an empty candidate.json beside it. Every vacancy scored
afterwards was scored against nothing and returned plausible numbers (0, 30, 50)
with no indication anywhere that the profile was gone. Found a day later by
comparing file sizes by hand.

The save was valid by every rule the code had: the payload's SHAPE was correct.
An unfilled wizard form is shape-valid and semantically catastrophic, and no
amount of type checking notices the difference.

Pure filesystem work — no network, no LLM, no browser.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

FULL = {
    "cases": [{"title": "MVP B2C"}, {"title": "AML contour"}],
    "skills": ["product discovery", "SQL"],
    "tools": ["Jira", "Figma"],
    "languages": ["ru", "en"],
    "identity": {"role": "Fintech PM"},
}
BLANK = {"cases": [], "skills": [], "tools": [], "languages": [], "identity": {}}

REAL_MD = """# Fintech PM
## Work Experience
### CIFRA | Product Manager | 2020 — 2026
Shipped the B2C storefront MVP and the AML contour.
## Skills
product discovery, SQL
"""

SKELETON_MD = """# MISSING — add your target role
## Skills
# EMPTY — add professional skills (e.g. platform thinking, API design, SQL)
## Work Experience
# EMPTY — add work history via wizard or edit candidate.md directly
"""


def run():
    from onboarding.profile_guard import (BACKUP_KEEP, backup_profile, check_destructive_save,
                                          existing_substance, list_backups,
                                          md_looks_like_skeleton, needs_migration, substance_of)
    failures = []
    root = Path(tempfile.mkdtemp(prefix="snaggd-guard-"))

    def profile(name, md=None, candidate=None):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        if md is not None:
            (d / "candidate.md").write_text(md, encoding="utf-8")
        if candidate is not None:
            (d / "candidate.json").write_text(json.dumps(candidate), encoding="utf-8")
        return d

    def check(cond, msg):
        if cond:
            print(f"  ✅ {msg}")
        else:
            failures.append(msg)

    # substance
    check(substance_of(FULL) == 8, "substance counts cases/skills/tools/languages")
    check(substance_of(BLANK) == 0, "an unfilled form has zero substance")
    check(substance_of({}) == 0 and substance_of(None) == 0, "missing/garbage payload is zero, not a crash")

    # skeleton recognition
    check(md_looks_like_skeleton(SKELETON_MD), "a rendered skeleton is recognised as one")
    check(not md_looks_like_skeleton(REAL_MD), "a real profile is not mistaken for a skeleton")
    check(md_looks_like_skeleton(""), "an empty file counts as a skeleton")

    # THE case: the 2026-08-11 wipe. Legacy profile — real markdown, no JSON yet,
    # which is why a JSON-only substance check would have sailed straight past it.
    d = profile("pm_legacy", md=REAL_MD)
    check(existing_substance(d) > 0, "a pre-candidate.json profile still reads as having content")
    check(check_destructive_save(d, BLANK) is not None, "the 2026-08-11 wipe is refused")
    check(needs_migration(d), "a legacy profile is flagged for migration, not opened blank")

    # The state a profile was left in AFTER the wipe and a manual candidate.md restore:
    # an emptied candidate.json beside a fully populated candidate.md. A JSON-first
    # reading calls this profile empty and lets the next wipe through — which is
    # the one situation the guard exists for, so it gets its own case.
    d = profile("pm_half_restored", md=REAL_MD, candidate=BLANK)
    check(existing_substance(d) > 0, "a restored markdown outweighs an emptied candidate.json")
    check(check_destructive_save(d, BLANK) is not None,
          "a half-restored profile is still protected from the next wipe")

    # ...and the same save against a structured profile
    d = profile("pm_json", md=REAL_MD, candidate=FULL)
    check(check_destructive_save(d, BLANK) is not None, "emptying a structured profile is refused")
    check(not needs_migration(d), "a migrated profile is not flagged again")

    # Legitimate saves must still go through, or onboarding breaks entirely.
    check(check_destructive_save(d, FULL) is None, "saving real content is allowed")
    reduced = {**FULL, "skills": ["SQL"], "tools": []}
    check(check_destructive_save(d, reduced) is None,
          "trimming a profile is an edit, not a wipe — still allowed")
    d_new = profile("brand_new")
    check(check_destructive_save(d_new, BLANK) is None,
          "first-run onboarding on an empty profile is allowed")
    d_skel = profile("skeleton_only", md=SKELETON_MD)
    check(check_destructive_save(d_skel, BLANK) is None,
          "overwriting a skeleton with a skeleton is allowed")

    # Backups: recoverable, findable, bounded.
    d = profile("backed_up", md=REAL_MD, candidate=FULL)
    stamp = backup_profile(d)
    sets = list_backups(d)
    check(len(sets) == 1 and sets[0]["stamp"] == stamp, "a save produces one backup set")
    check(sorted(sets[0]["files"]) == ["candidate.json", "candidate.md"],
          "both files land in the same set, recoverable as a pair")
    check(sets[0]["substance"] == 8, "a backup reports what it would restore")
    check((d / ".backups").is_dir() and not list((d).glob("*.bak")),
          "backups live in .backups/, not scattered next to the originals")

    for _ in range(BACKUP_KEEP + 3):
        backup_profile(d)
    check(len(list_backups(d)) == BACKUP_KEEP, f"backup sets are pruned to {BACKUP_KEEP}")
    newest = list_backups(d)
    check(newest == sorted(newest, key=lambda e: e["stamp"], reverse=True), "backups list newest first")

    shutil.rmtree(root, ignore_errors=True)
    return failures


def main():
    failures = run()
    if failures:
        for f in failures:
            print(f"  ❌ {f}")
        print(f"\n{len(failures)} failed")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
