#!/usr/bin/env python3
"""
Migrate one profile's legacy candidate.md (+ filters.json + job_preferences.md) -> candidate.json.

NOT the same job as scripts/migrate_profile.py (flat data/ -> data/profiles/<name>/ file copy,
schema-agnostic). This script converts one profile's OLD text-based wizard storage into the
new candidate.json schema (see .claude/working-notes/tz-pre-app-wizard-sprint.md Task 7).

Two categorically different reads, matching a permanent split the schema itself encodes —
not something invented for this migration, and not HH-specific:
  - Career FACTS (cases/skills/tools/languages/interests/pitch/identity) — genuinely extracted
    from document content. Connector-agnostic: reuses the same ResumeParser/LLM pipeline any
    future resume source (LinkedIn, Greenhouse, ...) would also go through.
  - Wizard PREFERENCES (career_profile/logistics/search/rules) — never resume-derived, even in
    the OLD system: these sections (when present) were always the wizard's own prior answers,
    just serialized as MD headers instead of JSON keys. Read back deterministically, no LLM —
    same category of operation as _require_candidate() reading candidate.json today.

Not every legacy candidate.md has both halves. Some profiles were never run through the old
wizard's preference-collection flow (candidate.md is closer to a raw resume than wizard output)
— in that case the deterministic read correctly comes back empty; there is nothing to recover,
and the script says so rather than silently producing a sparse-looking file.

Safety: NEVER writes directly into the live candidate.md/candidate.json. Default output is
side-by-side (candidate.migrated.{md,json}) for review/diff against the live version. --apply
promotes after an explicit confirmation if the live file already exists — same UX as
scripts/migrate_profile.py's own overwrite prompt.

Usage:
    python scripts/migrate_candidate.py --profile pm
    python scripts/migrate_candidate.py --profile pm --apply
"""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from profiles import PROFILES_DIR, resolve_profile
from onboarding.resume_parser import ResumeParser


def _llm_client():
    import os
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None
    from openai import OpenAI
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)


# ── Legacy wizard-state reader (deterministic, no LLM) ────────────────────────

def _split_sections(md_text: str) -> dict:
    """'## Header' -> {lowercased key: value} for 'key: value' lines in that section,
    plus '_raw' with the full section text (used for free-text sections like salary)."""
    sections: dict = {}
    current = None
    buf: list = []

    def flush():
        if current is None:
            return
        parsed: dict = {"_raw": "\n".join(buf).strip()}
        for line in buf:
            if ":" in line and not line.startswith("#"):
                k, _, v = line.partition(":")
                k, v = k.strip().lower(), v.strip()
                if k and v:
                    parsed[k] = v
        sections[current] = parsed

    for line in md_text.splitlines():
        if line.startswith("## "):
            flush()
            current = line[3:].strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    flush()
    return sections


def _read_legacy_wizard_state(md_text: str) -> dict:
    """Read the old candidate.md's wizard-authored sections, if present. Returns empty
    dicts for whatever wasn't there — that's a valid, honest result, not a parse failure."""
    sections = _split_sections(md_text)
    cp = sections.get("career profile", {})
    rw = sections.get("relocation & work format", {})
    old_identity = sections.get("identity", {})
    contacts_section = sections.get("contacts & personal", {})

    career_profile = {k: cp[k] for k in ("role_type", "edge", "aspiration") if cp.get(k)}

    rules = {}
    if cp.get("not_looking_for"):
        rules["penalize"] = [x.strip() for x in cp["not_looking_for"].split(",") if x.strip()]

    logistics = {}
    if rw.get("relocation") or rw.get("relocation_cities"):
        parts = [rw.get("relocation"), rw.get("relocation_cities")]
        logistics["relocation"] = " — ".join(p for p in parts if p)
    if rw.get("work_format_priority"):
        logistics["work_format"] = rw["work_format_priority"]

    search = {}
    salary_block = sections.get("desired salary", {}).get("_raw")
    if salary_block:
        search["salary"] = salary_block

    identity = {}
    if old_identity.get("name"):
        identity["name"] = old_identity["name"]
    if old_identity.get("role"):
        identity["role"] = old_identity["role"]
    contacts = [v for k, v in contacts_section.items() if k != "_raw" and v]
    if contacts:
        identity["contacts"] = contacts

    return {
        "career_profile": career_profile, "logistics": logistics,
        "search": search, "rules": rules, "identity": identity,
    }


_WIZARD_OWNED_SECTIONS = {"career profile", "relocation & work format", "desired salary",
                          "identity", "contacts & personal"}


def _strip_wizard_sections(md_text: str) -> str:
    """Remove sections the deterministic legacy-state reader already owns, before handing
    the rest to the LLM facts pass — otherwise the LLM sees preference text it has no schema
    field for and (correctly, per its own Rule F) dumps it into hints[], and can also bleed
    into pitch (a section like Career Profile reads a lot like an elevator pitch out of
    context). Keeps the two reads genuinely independent instead of just overwriting after."""
    out_lines: list = []
    skip = False
    for line in md_text.splitlines():
        if line.startswith("## "):
            skip = line[3:].strip().lower() in _WIZARD_OWNED_SECTIONS
            if skip:
                continue
        if not skip:
            out_lines.append(line)
    return "\n".join(out_lines)


def _parse_stop_categories(job_prefs_text: str) -> list:
    cats, in_block = [], False
    for line in job_prefs_text.splitlines():
        if line.startswith("stop_categories:"):
            in_block = True
        elif line.startswith("  - ") and in_block:
            cats.append(line[4:].strip())
        elif line.strip() and not line.startswith(" "):
            in_block = False
    return cats


# ── Migration ──────────────────────────────────────────────────────────────────

def migrate(profile: str, *, apply: bool) -> None:
    data_dir = PROFILES_DIR / profile
    md_path = data_dir / "candidate.md"
    if not md_path.exists():
        print(f"❌ {md_path} not found")
        sys.exit(1)

    old_text = md_path.read_text(encoding="utf-8")

    print("Extracting career facts (LLM — same pipeline Step 1 uses)...")
    client = _llm_client()
    if client is None:
        print("❌ LLM_API_KEY not set — needed for the facts-extraction half.")
        sys.exit(1)
    facts_text = _strip_wizard_sections(old_text)
    data = ResumeParser(client)._extract_with_llm(facts_text, md_path.name)

    print("Reading legacy wizard state (deterministic, no LLM)...")
    legacy = _read_legacy_wizard_state(old_text)

    # Identity: prefer the old file's own explicit answers over the LLM's re-derivation
    # from the same text — these were already-answered wizard questions, not a guess.
    identity = dict(data.identity or {})
    identity.update({k: v for k, v in legacy["identity"].items() if v})
    data.identity = identity
    data.career_profile = legacy["career_profile"]
    data.logistics = legacy["logistics"]
    data.search = legacy["search"]

    rules = dict(legacy["rules"])
    filters_path = data_dir / "filters.json"
    if filters_path.exists():
        try:
            filt = json.loads(filters_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            filt = {}
        stop = list(filt.get("stop_companies") or []) + list(filt.get("stop_title_keywords") or [])
        if stop:
            rules["stop"] = stop
        if filt.get("min_employer_rating") is not None:
            rules["min_employer_rating"] = filt["min_employer_rating"]

    prefs_path = data_dir / "job_preferences.md"
    if prefs_path.exists():
        cats = _parse_stop_categories(prefs_path.read_text(encoding="utf-8"))
        if cats:
            rules["stop"] = rules.get("stop", []) + cats
    data.rules = rules

    for case in data.cases:
        case["source"] = "migrated"
    data.source_file = f"{md_path.name} (legacy migration)"

    # hints were computed inside the LLM call, against identity BEFORE the deterministic merge
    # overlaid it — stale now (e.g. "add your name" even though name is present post-merge).
    # Recompute against the real final state. This does discard whatever genuine Rule-F hints
    # the LLM pass itself produced (stray unbucketable content) — accepted trade-off: this file
    # is a review artifact, not the live output, and honest hints against the ACTUAL final data
    # matter more here than preserving those.
    data.hints = []
    data = ResumeParser(None)._finalize(data)

    if not any([data.career_profile, data.logistics, data.search.get("salary"), data.rules.get("penalize")]):
        print("ℹ️  No legacy wizard-preference sections found in candidate.md itself —")
        print("   career_profile / logistics / search.salary / rules.penalize will be empty.")
        print("   This profile's old candidate.md looks like a raw resume, not wizard-processed")
        print("   output — nothing to recover from THAT file. (rules.stop / min_employer_rating")
        print("   are read separately from filters.json + job_preferences.md, unaffected by this.)")
        print("   Run Steps 2/5/6 after migrating to fill in the empty fields.")

    rendered_md = ResumeParser(None).to_md(data)
    out_json = data_dir / "candidate.migrated.json"
    out_md = data_dir / "candidate.migrated.md"
    payload = dataclasses.asdict(data)
    payload.pop("suggested_queries", None)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(rendered_md, encoding="utf-8")
    print(f"\n✓  {out_json}")
    print(f"✓  {out_md}")
    print(f"\nReview: diff {md_path} {out_md}")

    if not apply:
        print("\n(dry run — live candidate.md/candidate.json untouched. Re-run with --apply to promote.)")
        return

    live_json = data_dir / "candidate.json"
    live_md = data_dir / "candidate.md"
    if live_json.exists():
        ans = input(f"⚠  {live_json} already exists. Overwrite with the migrated version? [y/N] ")
        if ans.strip().lower() != "y":
            print("Aborted — migrated output stays at candidate.migrated.{json,md}, live files untouched.")
            return
    live_json.write_text(out_json.read_text(encoding="utf-8"), encoding="utf-8")
    live_md.write_text(out_md.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"✓  Applied → {live_json}")
    print(f"✓  Applied → {live_md}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy candidate.md -> candidate.json")
    parser.add_argument("--profile", required=True, help="Profile name (e.g. pm, support)")
    parser.add_argument("--apply", action="store_true",
                        help="Promote the migrated output into the live candidate.{json,md} "
                             "(default: side-by-side candidate.migrated.* only, for review)")
    args = parser.parse_args()

    profile = resolve_profile(args.profile)
    migrate(profile, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
