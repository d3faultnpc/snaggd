"""
Regression tests for onboarding/md_merge.py + ResumeParser.to_md().

What they pin down (2026-08-14): candidate.md carries preferences the ResumeData
schema has no field for — not_looking_for, relocation_cities, work_format_priority,
a salary table keyed by domain, and a note telling the form-filling agent how to
answer a salary field. A real profile on disk held every one of those.

The previous writer rendered the whole file from the schema and preserved only
what followed an end-marker comment. That profile had no markers, so "preserve
what follows the marker" preserved nothing: one wizard save would have replaced
all of it with the schema's poorer view of the same sections. profile_guard did
not cover it — it counts cases/skills/tools/languages, and a save carrying a
parsed resume passes that check while erasing every preference line.

The fixture below is that profile's real shape, kept verbatim in the parts that
matter, because a merge test written against invented input tests the merge the
author imagined rather than the file that exists.

Pure string work — no network, no LLM, no filesystem.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# The shape that broke: keys the schema knows nothing about, sitting inside
# sections the schema does manage.
LEGACY_MD = """# Fintech PM

## Career Profile
role_type: builder
edge: fintech PM who designs systems from inside the domain
aspiration: move deeper into AI-native / agentic product work
not_looking_for: process_management, pmm, outsource

## Relocation & Work Format
relocation: yes
relocation_cities: city A (current), city B (ok), abroad (ok)
work_format_priority: hybrid > remote > office

## Desired Salary
telecom: 100 000+ net
default: 120 000+ net
fintech: 150 000+ net
note: Match vacancy domain to pick the right range.

## Identity
name: Test Person
role: Product Manager
experience_years: 5
current_company: Northwind

## Skills
- platform thinking
- product discovery

## Additional
interests: Bitcoin Ordinals, UTXO data model
"""


def run():
    from onboarding import md_merge
    from onboarding.profile_guard import md_looks_like_skeleton
    from onboarding.resume_parser import ResumeData, ResumeParser

    failures = []

    def check(label, condition):
        print(f"  {'✅' if condition else '❌'} {label}")
        if not condition:
            failures.append(label)

    parser = ResumeParser(None)

    # ── The wipe this file exists to prevent ──────────────────────────────
    # A wizard save carrying a parsed resume: real cases and skills, and the
    # empty logistics/search a GUI wizard actually produces. `career_profile` was
    # part of this shape until 2026-08-25 and is gone with the human layer — the
    # wizard no longer collects role_type/edge/aspiration and the schema no longer
    # has a slot for them.
    wizard_save = ResumeData(
        identity={"name": "Test Person", "role": "Product Manager"},
        logistics={},
        search={},
        rules={"stop_categories": ["alpha", "beta"]},
        cases=[{"company": "Northwind", "role": "PM", "period": "2020 — 2026",
                "groups": [{"kind": "achievement",
                            "bullets": ["Storefront", "MVP shipped"]}]}],
        skills=["product discovery", "SQL"],
    )
    merged = parser.to_md(wizard_save, existing_content=LEGACY_MD)

    for key in ("not_looking_for", "relocation_cities", "work_format_priority",
                "experience_years", "current_company"):
        check(f"a wizard save preserves `{key}` — a key the schema has never heard of",
              key in merged)
    check("a wizard save preserves the domain-keyed salary table",
          "telecom: 100 000+ net" in merged and "fintech: 150 000+ net" in merged)
    check("a wizard save preserves the form-filling note under Desired Salary",
          "note: Match vacancy domain" in merged)

    # ── What the save is actually allowed to change ───────────────────────
    # Was checked on `role_type` until 2026-08-25 — a key that no longer exists. The
    # property is unchanged and still worth pinning; it just needs a key that does.
    check("a rendered key overwrites the existing one",
          "stop_categories: alpha, beta" in merged)
    # Written `skills: a, b` since 2026-08-17, not one bullet per line — Tools,
    # the section directly below it, had always been the keyed form, and one kind
    # of list deserves one shape. What this check is about is unchanged: the new
    # content is in and the old content is gone.
    check("a section the wizard owns is replaced by its new content",
          "skills: product discovery, SQL" in merged and "platform thinking" not in merged)
    check("a section the wizard has nothing for is left alone",
          "interests: Bitcoin Ordinals, UTXO data model" in merged)
    # Same substitution, same reason. `relocation_cities` is a key the renderer never
    # emits, in a section it does emit — which is exactly the shape this is about.
    check("an absent value does not erase the existing one",
          "relocation_cities: city A (current), city B (ok), abroad (ok)" in merged)
    check("Work Experience arrives from the save", "Northwind" in merged and "MVP shipped" in merged)

    # ── No scaffolding reaches the file (and therefore the system prompt) ──
    for placeholder in ("# HINT:", "# SKIPPABLE", "MISSING — add", "# EMPTY — "):
        check(f"the renderer never writes `{placeholder}` into candidate.md",
              placeholder not in merged)
    check("an empty section is omitted entirely, not written as a bare heading",
          "## Languages" not in merged and "## Tools" not in merged)

    # ── First save, no existing file ──────────────────────────────────────
    fresh = parser.to_md(wizard_save, existing_content="")
    check("a first save renders without an existing file", "## Skills" in fresh)
    check("a first save writes the title from identity.role", fresh.startswith("# Product Manager"))
    check("a first save with no role writes no title placeholder",
          not parser.to_md(ResumeData(skills=["SQL"]), "").startswith("# MISSING"))

    # ── One line per key ──────────────────────────────────────────────────
    # A section can end up holding the same key twice once lines are routed into it
    # from a dissolved heading — `## Contacts & Personal` arriving in an Identity
    # that already had a telegram. Both survived, then both were replaced by the
    # rendered value, so the duplicate outlived the heading that caused it.
    doubled = "## Identity\nname: X\ntelegram: first\ntelegram: second\n"
    once = md_merge.merge(doubled, "## Identity\nname: X\n")
    check("a key repeated in one section collapses to a single line",
          once.count("telegram:") == 1)
    check("…and it is the first one — the routed copy is the older statement",
          "telegram: first" in once)

    # ── Legacy markers migrate away ───────────────────────────────────────
    marked = (f"{md_merge.BLOCK_START}\n# Fintech PM\n\n## Skills\n- old skill\n"
              f"{md_merge.BLOCK_END}\n\nMy own notes below, untouched.\n")
    migrated = parser.to_md(wizard_save, existing_content=marked)
    check("the legacy start marker is dropped on the next save",
          md_merge.BLOCK_START not in migrated)
    check("free text after the legacy end marker still survives verbatim",
          "My own notes below, untouched." in migrated)

    # ── Section order is the file's own, not the renderer's ───────────────
    check("existing section order is preserved",
          migrated.index("## Skills") < migrated.index("## Work Experience"))

    # ── The guard's new predicate ─────────────────────────────────────────
    check("a real profile is not a skeleton", not md_looks_like_skeleton(LEGACY_MD))
    check("headings with nothing under them read as a skeleton",
          md_looks_like_skeleton("# Role\n\n## Skills\n\n## Work Experience\n"))
    check("a legacy placeholder file still reads as a skeleton",
          md_looks_like_skeleton("# MISSING — add your target role\n## Skills\n"
                                 "# EMPTY — add professional skills\n"))
    check("a legacy mid-line placeholder still reads as a skeleton",
          md_looks_like_skeleton("# Role\n## Career Profile\nrole_type: # SKIPPABLE — fill via wizard\n"))
    check("an emptied render reads as a skeleton",
          md_looks_like_skeleton(parser.to_md(ResumeData(), "")))

    print()
    print(f"{'❌ ' + str(len(failures)) + ' failed' if failures else '✅ all passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
