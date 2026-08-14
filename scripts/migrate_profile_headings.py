#!/usr/bin/env python3
"""Bring a profile's hand-written headings into the frame's vocabulary. Visibly.

Why this exists (2026-08-14):

candidate.md can be edited by hand, and a person naturally invents headings —
`## Side Projects`, `## Tools & Languages`, `## Contacts & Personal`. The frame
(onboarding/profile_frame.py) declares its own names for the same things. While
both spellings live in one file they are two different sections to every piece of
code that touches it, so uploading a CV writes the canonical one and leaves the
hand-written one beside it. That is not a cosmetic duplicate: it happened live,
and the file ended up asserting `english: B2` and `english: C1` at once, with the
model reading both as facts about the same person.

The rule a re-run signs, in the user's words: a CV OVERWRITES the sections that
were already known and ADDS the ones that were not. That rule cannot do its job
while the same section has two names, which is why one name per thing has to be
established first — and why this is a migration rather than a table of accepted
spellings. A synonym table would grow by a row per invented heading and leave the
underlying problem (heading string IS the semantic slot) exactly where it was.

Deliberately not automatic, and deliberately not clever:
  - it prints every heading and says which ones the frame declares;
  - the mapping is given on the command line, never guessed;
  - it backs the profile up first, prints the diff, and asks before writing.

    python scripts/migrate_profile_headings.py --profile pm
    python scripts/migrate_profile_headings.py --profile pm \\
        --drop "Side Projects" --drop "Tools & Languages" \\
        --rename "My Projects=Projects"
"""
import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from onboarding.md_merge import parse_sections  # noqa: E402
from onboarding.profile_frame import KIND_HEADINGS, RETIRED_HEADINGS  # noqa: E402
from onboarding.profile_guard import backup_profile  # noqa: E402
from profiles import PROFILES_DIR  # noqa: E402

# Sections that are part of the profile but hold no evidence — the frame's other
# half. Listed so the report can tell "declared" from "someone's own heading".
_DECLARED_NON_EVIDENCE = (
    "Identity", "Career Profile", "Relocation & Work Format",
    "Desired Salary", "Skills", "Tools", "Languages", "Additional",
)
DECLARED = set(_DECLARED_NON_EVIDENCE) | set(KIND_HEADINGS.values())


def render(preamble: list[str], sections: list[tuple[str, list[str]]]) -> str:
    out = [p for p in preamble if p.strip()]
    for heading, body in sections:
        body = list(body)
        while body and not body[-1].strip():
            body.pop()
        if not body:
            continue
        out += ["", f"## {heading}"] + body
    return "\n".join(out).lstrip("\n") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--drop", action="append", default=[], metavar="HEADING",
                    help="remove this section — use when a canonical section already "
                         "holds the same thing and is the newer statement of it")
    ap.add_argument("--rename", action="append", default=[], metavar="FROM=TO",
                    help="rename a section to a frame heading, keeping its content")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    data_dir = PROFILES_DIR / args.profile
    md_path = data_dir / "candidate.md"
    if not md_path.exists():
        print(f"❌ no candidate.md in {data_dir}")
        return 1

    original = md_path.read_text(encoding="utf-8")
    preamble, sections = parse_sections(original)

    print(f"\nProfile: {args.profile}   ({len(sections)} sections)\n")
    for heading, body in sections:
        if heading in DECLARED:
            state = "declared"
        elif heading in RETIRED_HEADINGS:
            state = "retired — migrates on the next save on its own"
        else:
            state = "OWN HEADING — invisible to every structured view"
        print(f"  {'✅' if heading in DECLARED else '⚠️ '} ## {heading:<26} {state}  ({len(body)} lines)")

    renames = {}
    for pair in args.rename:
        if "=" not in pair:
            print(f"\n❌ --rename wants FROM=TO, got {pair!r}")
            return 1
        src, dst = (p.strip() for p in pair.split("=", 1))
        if dst not in DECLARED:
            print(f"\n❌ {dst!r} is not a heading the frame declares. One of:\n   "
                  + "\n   ".join(sorted(DECLARED)))
            return 1
        renames[src] = dst

    known = {h for h, _ in sections}
    for heading in list(args.drop) + list(renames):
        if heading not in known:
            print(f"\n❌ no section called {heading!r} in this profile")
            return 1

    if not args.drop and not renames:
        print("\nNothing to change — pass --drop / --rename. Nothing was written.")
        return 0

    migrated: list[tuple[str, list[str]]] = []
    for heading, body in sections:
        if heading in args.drop:
            continue
        target = renames.get(heading, heading)
        for i, (existing, existing_body) in enumerate(migrated):
            if existing == target:
                # Renaming onto a section that already exists: the two bodies join
                # under one heading rather than one silently winning. Whichever line
                # is wrong is now next to the right one, where a person can see it.
                migrated[i] = (existing, existing_body + [""] + list(body))
                break
        else:
            migrated.append((target, list(body)))

    updated = render(preamble, migrated)
    diff = list(difflib.unified_diff(original.splitlines(), updated.splitlines(),
                                     "before", "after", lineterm="", n=1))
    if not diff:
        print("\nThe file already looks like this. Nothing was written.")
        return 0

    print("\n" + "─" * 60)
    print("\n".join(diff))
    print("─" * 60)
    print(f"\n{len(original.splitlines())} lines → {len(updated.splitlines())}")

    if not args.yes and input("\nApply? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing was written.")
        return 0

    stamp = backup_profile(data_dir, filenames=("candidate.md",))
    md_path.write_text(updated, encoding="utf-8")
    print(f"✓ written. Previous version in {data_dir / '.backups'} ({stamp})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
