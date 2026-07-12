#!/usr/bin/env python3
"""
Onboarding wizard — run once per profile before first main.py session.
Produces: data/profiles/<name>/{candidate.md, candidate.json, job_preferences.md, tone_of_voice.md}

Usage:
    python onboarding/wizard.py                            # prompts for a profile name, runs steps 1-7
    python onboarding/wizard.py --profile pm                # full onboarding, named up front
    python onboarding/wizard.py --resume path/to/cv.pdf     # skip file prompt (step 1)
    python onboarding/wizard.py --profile pm --step 3       # redo a single step (name required if 2+ profiles)
    python onboarding/wizard.py --setup-keys                # .env only (API key/model/headless/limits), profile-agnostic
    python onboarding/wizard.py --list-profiles

Profile resolution (same rule as main.py / api.py, see profiles.py): editing an
existing profile auto-selects when there's only one and requires --profile when
there are several; creating a new one always asks for (or takes) a name — there
is no flat/legacy data dir fallback.
"""

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ── Pre-parse --profile before CONFIG import (same pattern as main.py) ────────
from profiles import PROFILES_DIR, list_profiles, resolve_profile

_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--profile", type=str, default=None)
_pre.add_argument("--list-profiles", action="store_true")
_pre.add_argument("--setup-keys", action="store_true")
_pre.add_argument("--step", type=int, choices=range(1, 8), default=None)
_pre_args, _ = _pre.parse_known_args()

if _pre_args.list_profiles:
    profiles = list_profiles()
    print("Available profiles:" if profiles else "No profiles yet.")
    for p in profiles:
        print(f"  {p:<20} configured")
    sys.exit(0)

# Same profile law as main.py — no writes to a flat/legacy data dir, ever.
# The three branches below cover every entry point into this file:
if _pre_args.setup_keys:
    # .env is global — not profile-scoped, nothing to resolve.
    _active_profile = None
elif _pre_args.step:
    # Editing a single step of an EXISTING profile: same selection rule as main.py
    # (auto-select if there's only one, otherwise --profile <name> is required).
    _active_profile = resolve_profile(_pre_args.profile)
    os.environ["DATA_DIR"] = str(PROFILES_DIR / _active_profile)
elif _pre_args.profile:
    # Full onboarding with an explicit name — create it (or reuse if it exists).
    _active_profile = _pre_args.profile
    os.environ["DATA_DIR"] = str(PROFILES_DIR / _active_profile)
else:
    # Full onboarding, no name given — ask once, up front. This is a create
    # operation, so there's nothing to auto-select among; better to ask now
    # than to silently write into a legacy flat data/ directory.
    _name = input("Profile name for this resume (used as data/profiles/<name>/, e.g. 'pm'): ").strip()
    while not _name:
        _name = input("Profile name (required): ").strip()
    _active_profile = _name
    os.environ["DATA_DIR"] = str(PROFILES_DIR / _active_profile)

from config import CONFIG
from onboarding.resume_parser import ResumeParser, ResumeData


# ── Helpers ───────────────────────────────────────────────────────────────────

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or default


def ask_list(prompt: str) -> list:
    print(f"{prompt} (one item per line, empty line to finish):")
    items = []
    while True:
        line = input("  > ").strip()
        if not line:
            break
        items.append(line)
    return items


def section(title: str):
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print('─' * 50)


def _llm_client():
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None
    from openai import OpenAI
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)


# ── Helpers: file patchers ────────────────────────────────────────────────────

def _patch_filters_json(data_dir: Path, *, stop_companies=None, stop_title_keywords=None,
                         min_employer_rating=None) -> None:
    """Merge wizard-collected hard-filter rules into data/filters.json."""
    path = data_dir / "filters.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        data = {}
    if not data.get("_comment"):
        data["_comment"] = "Machine-only stop rules. Edited by wizard/settings. Never sent to LLM."
    data.setdefault("stop_title_keywords", [])
    data.setdefault("stop_companies", [])
    if stop_companies is not None:
        data["stop_companies"] = stop_companies
    if stop_title_keywords is not None:
        data["stop_title_keywords"] = stop_title_keywords
    if min_employer_rating is not None:
        data["min_employer_rating"] = min_employer_rating
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_salary_to_candidate(data_dir: Path, salary_text: str) -> None:
    """Set search.salary and re-render via to_md() (schema-aware path), or fall back to
    raw text patching on candidate.md for profiles that predate candidate.json."""
    json_path = data_dir / "candidate.json"
    md_path = data_dir / "candidate.md"

    if json_path.exists():
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            raw.setdefault("search", {})["salary"] = salary_text
            data = ResumeData(**raw)  # validate BEFORE writing anything to disk
            existing = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
            rendered_md = ResumeParser(None).to_md(data, existing_content=existing)
            json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            md_path.write_text(rendered_md, encoding="utf-8")
            return
        except Exception as e:
            print(f"⚠  candidate.json update failed ({e}) — falling back to text patch on candidate.md")

    # Legacy fallback: no candidate.json yet — raw text patch on candidate.md's Desired Salary section
    if not md_path.exists():
        return
    content = md_path.read_text(encoding="utf-8")
    marker = "## Desired Salary"
    new_section = f"\n{marker}\n{salary_text}\n"
    if marker in content:
        lines, skip = [], False
        for line in content.splitlines():
            if line.strip() == marker:
                skip = True
                continue
            if skip and line.startswith("## "):
                skip = False
            if not skip:
                lines.append(line)
        content = "\n".join(lines)
    md_path.write_text(content.rstrip("\n") + new_section, encoding="utf-8")


# ── Step 1: Resume parse (new 7-step model) ───────────────────────────────────
#
# LLM-driven only — no interactive Q&A here (that's steps 2/5/6). Owns fields the
# parser actually derives from the CV: cases, skills, tools, languages, interests,
# hints, target_market, locale, identity, pitch. Never touches career_profile/
# logistics/search/rules — those are wizard-filled only, never CV content — and
# are preserved verbatim from an existing candidate.json if one exists.

def _read_candidate_json(data_dir: Path) -> dict | None:
    path = data_dir / "candidate.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _require_candidate(data_dir: Path) -> ResumeData | None:
    """Steps 2-6 all edit an existing candidate.json — none of them can run before Step 1."""
    existing = _read_candidate_json(data_dir)
    if not existing:
        print("⚠  No candidate.json yet — run Step 1 first (parses your resume).")
        return None
    try:
        data = ResumeData(**existing)
    except TypeError as e:
        print(f"⚠  candidate.json doesn't match the expected schema ({e}) — "
              f"re-run Step 1, or fix the file by hand.")
        return None
    expected_types = {"identity": dict, "career_profile": dict, "logistics": dict,
                       "search": dict, "rules": dict, "cases": list, "skills": list,
                       "tools": list, "languages": list, "interests": list,
                       "suggested_queries": list, "hints": list}
    for field_name, expected_type in expected_types.items():
        if not isinstance(getattr(data, field_name), expected_type):
            print(f"⚠  candidate.json field '{field_name}' has the wrong type "
                  f"(expected {expected_type.__name__}) — re-run Step 1, or fix the file by hand.")
            return None
    return data


def _write_candidate(data_dir: Path, data: ResumeData) -> None:
    """Shared disk-write for Resume-derived data: candidate.md (via to_md(), preserves any
    free-text user section below the managed-block marker) + candidate.json + suggested_queries.txt."""
    md_out = data_dir / "candidate.md"
    existing = md_out.read_text(encoding="utf-8") if md_out.exists() else ""
    md_out.write_text(ResumeParser(None).to_md(data, existing_content=existing), encoding="utf-8")
    print(f"\n✓  Saved → {md_out}")

    json_out = data_dir / "candidate.json"
    payload = dataclasses.asdict(data)
    payload.pop("suggested_queries", None)  # parser convenience field, not part of the schema
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓  Saved → {json_out}")

    if data.suggested_queries:
        sq_out = data_dir / "suggested_queries.txt"
        sq_out.write_text("\n".join(data.suggested_queries) + "\n", encoding="utf-8")
        print(f"✓  Search suggestions → {sq_out}")
        for q in data.suggested_queries:
            print(f"   · {q}")


def step_1_resume(resume_path: Path | None = None) -> ResumeData | None:
    section("Step 1 — Resume")
    print("Parses your resume into structured data (cases, skills, tools, languages).")
    print("Supported: PDF, DOCX, PNG, JPG, MD, TXT\n")

    if resume_path is None:
        raw = ask("Path to resume file")
        resume_path = Path(raw) if raw else None

    if not resume_path or not resume_path.exists():
        print(f"⚠  File not found: {resume_path}" if resume_path else
              "⚠  No file given — Step 1 needs a resume (manual field entry lives in Steps 2-6, not here).")
        return None

    client = _llm_client()
    if client is None:
        print("⚠  LLM_API_KEY not set — Step 1 requires the LLM parser. "
              "Run --setup-keys first (or set LLM_API_KEY in .env).")
        return None

    print(f"\nParsing {resume_path.name}...")
    try:
        data = ResumeParser(client).parse_file(resume_path)
    except Exception as e:
        print(f"⚠  Parse error: {e}")
        return None

    existing = _read_candidate_json(CONFIG.data_dir)
    if existing:
        preserved = [k for k in ("career_profile", "logistics", "search", "rules") if existing.get(k)]
        for key in preserved:
            setattr(data, key, existing[key])
        if preserved:
            print(f"   (preserved from existing candidate.json: {', '.join(preserved)})")

    if data.hints:
        print("   Hints:")
        for h in data.hints:
            print(f"   · {h}")

    _write_candidate(CONFIG.data_dir, data)
    return data


# ── Step 2: Identity ───────────────────────────────────────────────────────────
# Review/edit pass over identity.* + pitch — Step 1 seeds these from the CV, this step lets
# the user correct/fill them. Current values shown as defaults; Enter keeps them unchanged.

def step_2_identity() -> bool:
    section("Step 2 — Identity")
    data = _require_candidate(CONFIG.data_dir)
    if data is None:
        return False

    print("Current values shown in [brackets] — press Enter to keep, or type to replace.\n")
    identity = dict(data.identity or {})
    identity["name"] = ask("Full name", identity.get("name") or "") or None
    identity["role"] = ask("Target role / title", identity.get("role") or "") or None
    identity["location"] = ask("City / location", identity.get("location") or "") or None

    contacts = list(identity.get("contacts") or [])
    print(f"\nCurrent contacts: {contacts or '(none)'}")
    if not ask("Keep as-is? yes/no", "yes").lower().startswith("y"):
        contacts = ask_list("Contact links (Telegram/GitHub/LinkedIn/email/phone)")
    identity["contacts"] = contacts
    data.identity = identity

    print(f"\nCurrent pitch: {data.pitch or '(none)'}")
    data.pitch = ask("Pitch — 1-2 sentence summary", data.pitch or "") or None

    _write_candidate(CONFIG.data_dir, data)
    return True


# ── Steps 3 & 4: History / Projects & Credentials ─────────────────────────────
# Shared case-review UI, split by type: Step 3 = employment+education, Step 4 = the
# project-family types. Split mirrors resume_parser.py's own _PROJECT_TYPES bucketing
# exactly, so wizard-side and render-side classification never drift apart.

_PROJECT_TYPES = {"project", "certification", "publication", "volunteering", "research"}


def _case_summary(case: dict) -> str:
    parts = [x for x in [case.get("company"), case.get("role"), case.get("period")] if x]
    return " | ".join(parts) if parts else "(untitled case)"


def _edit_case_fields(case: dict) -> dict:
    case = dict(case)
    case["type"] = ask("Type (employment/education/project/certification/publication/volunteering/research)",
                        case.get("type") or "employment")
    case["company"] = ask("Company / institution / project name", case.get("company") or "") or None
    case["role"] = ask("Role / degree / project role", case.get("role") or "") or None
    case["period"] = ask("Period (e.g. 2022–2024)", case.get("period") or "") or None
    case["domain"] = ask("Domain/industry (Enter to skip)", case.get("domain") or "") or None
    case["url"] = ask("URL (Enter to skip)", case.get("url") or "") or None

    highlights = list(case.get("highlights") or [])
    print(f"Current highlights: {len(highlights)}")
    for h in highlights:
        print(f"  · {h.get('label') or '(no label)'}: {', '.join(h.get('results') or [])}")
    if ask("Add a highlight? yes/no", "no").lower().startswith("y"):
        while True:
            label = ask("  Highlight label (project/initiative name, Enter to finish)")
            if not label:
                break
            h_ctx = ask("  Context (1-2 sentences)")
            results = ask_list("  Metrics/results")
            highlights.append({"label": label, "context": h_ctx or None, "results": results})
    case["highlights"] = highlights

    responsibilities = list(case.get("responsibilities") or [])
    if not ask(f"Keep responsibilities as-is? (currently {len(responsibilities)}) yes/no", "yes").lower().startswith("y"):
        responsibilities = ask_list("Responsibilities (ongoing duties, no single metric)")
    case["responsibilities"] = responsibilities

    return case


def _review_cases(data: ResumeData, *, projects_only: bool) -> ResumeData:
    matching_idx = [i for i, c in enumerate(data.cases) if (c.get("type") in _PROJECT_TYPES) == projects_only]

    print("\nCurrent entries:")
    if matching_idx:
        for n, i in enumerate(matching_idx, 1):
            print(f"  {n}. {_case_summary(data.cases[i])}")
    else:
        print("  (none)")

    while True:
        choice = ask(f"\nEdit entry # (1-{len(matching_idx)}), 'new' to add, Enter to move on").strip()
        if not choice:
            break
        if choice.lower() == "new":
            new_case = _edit_case_fields({})
            data.cases.append(new_case)
            matching_idx.append(len(data.cases) - 1)
            print(f"  {len(matching_idx)}. {_case_summary(new_case)}")
            continue
        try:
            n = int(choice)
            if n < 1 or n > len(matching_idx):
                raise ValueError
            i = matching_idx[n - 1]
        except ValueError:
            print("  ⚠  Invalid choice")
            continue
        data.cases[i] = _edit_case_fields(data.cases[i])
        print(f"  ✓  Updated: {_case_summary(data.cases[i])}")

    return data


def step_3_history() -> bool:
    section("Step 3 — History (Employment & Education)")
    data = _require_candidate(CONFIG.data_dir)
    if data is None:
        return False
    data = _review_cases(data, projects_only=False)
    _write_candidate(CONFIG.data_dir, data)
    return True


def step_4_projects() -> bool:
    section("Step 4 — Projects & Credentials")
    data = _require_candidate(CONFIG.data_dir)
    if data is None:
        return False
    data = _review_cases(data, projects_only=True)
    _write_candidate(CONFIG.data_dir, data)
    return True


# ── Step 5: Skills & Career Profile ────────────────────────────────────────────
# TZ Task 6 table lists this step's old-block source as "Block A + Block C (tone)" but its
# stated outputs are only skills[]/tools[]/languages[]/career_profile — tone_of_voice.md isn't
# part of the candidate.json schema at all. Reading: fold Block C's tone collection in here as
# an optional tail (same behavior as today, own file, unrelated to the JSON) rather than give it
# a separate step. Flagging this interpretation — the TZ doesn't spell it out either way.

def step_5_skills() -> bool:
    section("Step 5 — Skills & Career Profile")
    data = _require_candidate(CONFIG.data_dir)
    if data is None:
        return False

    print(f"Current skills ({len(data.skills)}): {', '.join(data.skills) or '(none)'}")
    if not ask("Keep as-is? yes/no", "yes").lower().startswith("y"):
        data.skills = ask_list("Professional skills")

    print(f"\nCurrent tools ({len(data.tools)}): {', '.join(data.tools) or '(none)'}")
    if not ask("Keep as-is? yes/no", "yes").lower().startswith("y"):
        data.tools = ask_list("Tools (Jira, Figma, SQL, ...)")

    print(f"\nCurrent languages: {data.languages or '(none)'}")
    if not ask("Keep as-is? yes/no", "yes").lower().startswith("y"):
        langs = []
        print("Languages (one per line as 'lang: level', empty line to finish):")
        while True:
            line = input("  > ").strip()
            if not line:
                break
            lang, _, level = line.partition(":")
            langs.append({"lang": lang.strip(), "level": level.strip() or None, "note": None})
        data.languages = langs

    print("\n🎯 Career self-profile (shapes scoring and cover letter angle — Enter to keep current):")
    cp = dict(data.career_profile or {})
    role_examples = ["builder", "operator", "strategic", "ops", "head"]
    cp["role_type"] = ask(f"Role type (examples: {'/'.join(role_examples)}, or your own)", cp.get("role_type") or "") or None
    cp["edge"] = ask("Your edge vs other candidates in 1 sentence", cp.get("edge") or "") or None
    cp["aspiration"] = ask("Direction you want to move toward (optional)", cp.get("aspiration") or "") or None
    data.career_profile = cp

    _write_candidate(CONFIG.data_dir, data)

    if ask("\nSet/update tone of voice for cover letters now? yes/no", "yes").lower().startswith("y"):
        block_c()

    return True


# ── Block B: Job preferences → job_preferences.md + .env ─────────────────────
# Internal helper for step_6_search_rules() now — no longer independently CLI-exposed
# (used to be --block b; using it directly would write the legacy files without syncing
# candidate.json, reintroducing the drift this whole schema exists to prevent).

def _pick_auto_wise_link() -> dict | None:
    """Reads hh_resumes.json (written by login.py) and returns a resolved wise-link entry.

    Returns {"url": ..., "title": ...} or None if file missing / user skips.
    """
    resumes_path = Path(CONFIG.cookies_path).parent / "hh_resumes.json"
    if not resumes_path.exists():
        return None
    try:
        resumes = json.loads(resumes_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not resumes:
        return None

    if len(resumes) == 1:
        r = resumes[0]
        print(f"✅ Resume auto-detected: {r['title']}")
    else:
        print("Multiple resumes found — choose which one to use for vacancy search:")
        for i, r in enumerate(resumes, 1):
            print(f"  {i}. {r['title']}")
        choice = ask("Resume number", "1")
        try:
            r = resumes[int(choice.strip()) - 1]
        except (ValueError, IndexError):
            print("⚠  Invalid choice — falling back to manual URL input")
            return None

    url = f"https://hh.ru/search/vacancy?resume={r['uuid']}&from=resumelist"
    return {"url": url, "title": r["title"]}


def block_b(append: bool = False) -> dict:
    section("Block B — Job preferences")
    print("This shapes the search URLs and helps the agent score vacancies.")
    print("You can add multiple searches — different roles or resume directions.")
    if append:
        print("Mode: APPEND — new URLs will be added to existing search_urls.txt\n")
    else:
        print()

    from onboarding.url_builder import build_hh_url

    stop_co   = ask_list("Stop-companies (companies you don't want to apply to)")
    stop_kw   = ask_list("Stop-keywords in vacancy titles (e.g. junior, intern)")
    stop_cats = ask_list("Stop industries/domains — LLM semantic filter (e.g. gambling, adult, MLM, outsource)")
    min_rating_str = ask("Min employer HH rating to apply (1.0–5.0, e.g. 3.6, Enter = no filter)")

    # Load suggested queries from Step 1's parse (if available)
    sq_path = CONFIG.data_dir / "suggested_queries.txt"
    suggested = []
    if sq_path.exists():
        suggested = [l.strip() for l in sq_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if suggested:
        print("Suggested search queries from your resume:")
        for i, q in enumerate(suggested, 1):
            print(f"  {i}. {q}")
        print()

    # Collect one or more search directions
    _auto = _pick_auto_wise_link()
    searches = []
    while True:
        print(f"\n── Search #{len(searches) + 1} ──")

        # First iteration with auto-detected wise link: skip URL building, just collect prefs
        if not searches and _auto:
            print(f"  🔗 Wise link: {_auto['url'][:72]}...")
            default_role = suggested[0] if suggested else _auto["title"]
            role   = ask("Target role (for vacancy scoring)", default_role)
            salary = ask("Minimum salary, RUB (press Enter to skip)")
            remote = ask("Work format: office / remote / hybrid", "hybrid")
            searches.append({
                "role": role, "city": "", "salary": salary,
                "remote": remote, "scope": "everywhere", "flexible": False,
                "url": _auto["url"],
            })
            print(f"✓  URL: {_auto['url']}")
            another = ask("\nAdd keyword-based search directions too? yes / no", "no")
            if not another.lower().startswith("y"):
                break
            continue

        # Normal text-based search (no auto-detect, or additional directions)
        if not searches:
            print("  💡 Tip: use your HH magic link for best results.")
            print("     HH → My Resumes → [resume] → 'Подобрать вакансии' → copy URL.")
            print("     The link has your resume embedded — no CV selection step needed.")
        default_role = suggested[len(searches)] if len(searches) < len(suggested) else ""
        role   = ask("Target role (e.g. Product Manager)", default_role)
        city   = ask("City (e.g. Moscow, remote)", "Moscow")
        salary = ask("Minimum salary, RUB (press Enter to skip)")
        remote = ask("Work format: office / remote / hybrid", "hybrid")

        print("  Search scope:")
        print("    1. Title only        — role must appear in vacancy title (precise)")
        print("    2. Title + body      — role anywhere in description (broad, scorer filters)")
        scope_choice = ask("  Scope [1/2]", "2")
        search_scope = "name" if scope_choice.strip() == "1" else "everywhere"

        flexible = False
        if remote.lower() in ("hybrid", "office"):
            flex_ans = ask("Flexible/temporary schedule only? yes / no (warning: cuts ~95% of listings)", "no")
            flexible = flex_ans.lower().startswith("y")

        url = build_hh_url(role=role, city=city, salary=salary,
                           remote=remote, search_scope=search_scope, flexible=flexible)
        searches.append({"role": role, "city": city, "salary": salary,
                         "remote": remote, "scope": search_scope, "flexible": flexible, "url": url})
        print(f"✓  URL: {url}")

        another = ask("\nAdd another search direction? yes / no", "no")
        if not another.lower().startswith("y"):
            break

    # Save search URLs (append or overwrite)
    urls_out = CONFIG.search_urls_path
    new_urls = "\n".join(s["url"] for s in searches) + "\n"
    if append and urls_out.exists():
        existing = urls_out.read_text(encoding="utf-8").rstrip("\n")
        urls_out.write_text(existing + "\n" + new_urls, encoding="utf-8")
        total = len([l for l in urls_out.read_text(encoding="utf-8").splitlines() if l.strip()])
        print(f"\n✓  {len(searches)} URL(s) appended → {urls_out} ({total} total)")
    else:
        urls_out.write_text(new_urls, encoding="utf-8")
        print(f"\n✓  {len(searches)} search URL(s) saved → {urls_out}")

    # Desired salary — free-form, written to candidate.md for LLM context
    print("\n💰 Desired salary — used when the agent fills salary fields on application forms.")
    print("   Tip: wider range gives the model more freedom to match market rates.")
    print("   Examples: 'от 150 000 руб.' · 'default 150 000, tech 200 000' · '120 000–250 000'")
    salary_hint = ask("Salary expectations (free form, Enter to skip)")

    # Save preferences (used by LLM for vacancy scoring)
    roles = ", ".join(s["role"] for s in searches)
    salary_min = next((s["salary"] for s in searches if s["salary"]), "not set")
    work_format = searches[0]["remote"]
    lines = [
        "# job_preferences.md",
        f"roles: {roles}",
        f"salary_min: {salary_min}",
        f"work_format: {work_format}",
    ]
    if stop_co:
        lines += ["stop_companies:"] + [f"  - {c}" for c in stop_co]
    if stop_kw:
        lines += ["stop_keywords:"] + [f"  - {k}" for k in stop_kw]
    if stop_cats:
        lines += ["stop_categories:"] + [f"  - {c}" for c in stop_cats]

    prefs_out = CONFIG.data_dir / "job_preferences.md"
    prefs_out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓  Preferences saved → {prefs_out}")

    # Write hard filter rules to filters.json (stop_companies + min_rating)
    min_rating: float | None = None
    if min_rating_str:
        try:
            min_rating = float(min_rating_str)
        except ValueError:
            print(f"⚠  Could not parse rating '{min_rating_str}' — skipping")
    _patch_filters_json(
        CONFIG.data_dir,
        stop_companies=[c.lower() for c in stop_co] if stop_co else [],
        stop_title_keywords=[k.lower() for k in stop_kw] if stop_kw else [],
        min_employer_rating=min_rating,
    )
    print(f"✓  Filters saved → {CONFIG.data_dir / 'filters.json'}")

    # Append desired salary to candidate.md
    if salary_hint:
        _append_salary_to_candidate(CONFIG.data_dir, salary_hint)
        print(f"✓  Salary added → {CONFIG.data_dir / 'candidate.md'}")

    return {
        "stop_companies": stop_co, "stop_keywords": stop_kw, "stop_categories": stop_cats,
        "min_employer_rating": min_rating, "searches": searches, "salary": salary_hint,
        "wise_link": _auto["url"] if _auto else None,
    }


# ── Step 6: Search & Rules ─────────────────────────────────────────────────────
#
# OPEN EDGE, flagged not solved: adapter.py's 3-tier stop enforcement (title/company/rating/
# semantic) reads filters.json at runtime TODAY — candidate.json's rules.* isn't consumed by
# anything yet (TZ Task 7's job). So this step calls block_b() unchanged (real files, zero
# regression risk to the live apply loop) and additionally dual-writes the equivalent data into
# candidate.json's new fields. That write is additive only until Task 7 wires adapter.py to read
# from candidate.json instead — until then this step's rules.*/search.* output isn't live.
#
# Same edge for rules.min_match: TZ calls moving MIN_SCORE from a global env var to this
# per-profile field "a real behavior change — Gate 2 item" on its own. Not doing that wiring here.

def step_6_search_rules(append: bool = False) -> bool:
    section("Step 6 — Search & Rules")
    data = _require_candidate(CONFIG.data_dir)
    if data is None:
        return False

    collected = block_b(append=append)

    print("\n📋 A couple more fields for candidate.json (adapter.py doesn't read these yet):")
    logistics = dict(data.logistics or {})
    logistics["relocation"] = ask("Relocation — where are you open to? (free text, Enter to skip)",
                                    logistics.get("relocation") or "") or None
    logistics["work_format"] = ask("Work format priority (e.g. 'hybrid > remote > office')",
                                     logistics.get("work_format") or "") or None
    data.logistics = logistics

    rules = dict(data.rules or {})
    rules["stop"] = collected["stop_companies"] + collected["stop_keywords"] + collected["stop_categories"]
    penalize = ask_list("NOT looking for (soft-skip, e.g. process_management, pmm, outsource)")
    if penalize:
        rules["penalize"] = penalize
    if collected["min_employer_rating"] is not None:
        rules["min_employer_rating"] = collected["min_employer_rating"]
    data.rules = rules

    search = dict(data.search or {})
    if collected["salary"]:
        search["salary"] = collected["salary"]
    search["queries"] = [s["role"] for s in collected["searches"] if s.get("role")]
    if collected["wise_link"]:
        search["wise_link"] = collected["wise_link"]
    data.search = search

    _write_candidate(CONFIG.data_dir, data)
    return True


# ── Step 7: HH Connect ──────────────────────────────────────────────────────────
#
# login.py is a standalone top-level script (opens a real, visible browser window,
# blocks until the user closes it, then scrapes /applicant/resumes) — not refactored into
# an importable function here, since that would mean touching already-working Playwright/
# cookie-lifecycle code just to "surface" it. Runs as a subprocess instead.
#
# Cookies + hh_resumes.json are shared across ALL profiles (one HH account, see
# config.py's cookies_path — fixed at data/hh_cookies.json, not derived from DATA_DIR),
# even though this is step 7 of a per-profile flow. Numbered last per the TZ, but note:
# step_6's wise-link auto-detect (_pick_auto_wise_link) reads hh_resumes.json, which only
# exists AFTER this step has run once — so on someone's very first onboarding, running the
# full 1-7 sequence in order means Step 6 won't have auto-detect data yet. Not fixed here
# (would mean reordering the TZ's own numbered sequence) — flagging for the DoD conversation.

def step_7_hh_connect() -> bool:
    section("Step 7 — HH Connect")
    print("Opens a real browser window — log into hh.ru, then close the window.")
    print("Cookies + your resume list get saved (shared across all profiles, one HH account).\n")

    if not ask("Run HH login now? yes/no", "yes").lower().startswith("y"):
        print("⏭  Skipped — run `python login.py` manually later.")
        return False

    login_script = Path(__file__).parent.parent / "login.py"
    result = subprocess.run([sys.executable, str(login_script)])
    if result.returncode != 0:
        print("⚠  Login didn't complete successfully (see output above).")
        return False

    # login.py's exit code only reflects the cookie-save phase — the resume-scrape phase
    # catches its own exceptions and still exits 0 on failure. Check its actual output
    # artifact instead of trusting the returncode alone for the part we care about here.
    resumes_path = Path(CONFIG.cookies_path).parent / "hh_resumes.json"
    try:
        resumes = json.loads(resumes_path.read_text(encoding="utf-8")) if resumes_path.exists() else []
    except (json.JSONDecodeError, OSError):
        resumes = []
    if not resumes:
        print("⚠  Cookies saved, but no resumes were captured (see output above) — "
              "you'll need to enter your wise link manually in Step 6.")
        return False

    print(f"✓  HH Connect done — {len(resumes)} resume(s) found.")
    return True


# ── Block C: Tone of voice → tone_of_voice.md ────────────────────────────────
# Internal helper for step_5_skills() now — no longer independently CLI-exposed.

def block_c() -> bool:
    section("Block C — Tone of voice")
    print("Controls how cover letters sound.\n")

    formality = ask("Style: formal / semi-formal / friendly", "semi-formal")
    sample    = ""
    print("\nPaste a sample cover letter you like (press Enter twice when done, or just Enter to skip):")
    lines = []
    while True:
        line = input()
        if line == "" and (not lines or lines[-1] == ""):
            break
        lines.append(line)
    if lines:
        sample = "\n".join(lines).strip()

    content = [
        "# tone_of_voice.md",
        f"formality: {formality}",
    ]
    if sample:
        content += ["", "sample_cover: |", *("  " + l for l in sample.split("\n"))]

    out = CONFIG.data_dir / "tone_of_voice.md"
    out.write_text("\n".join(content), encoding="utf-8")
    print(f"\n✓  Saved → {out}")
    return True


# ── Block D: .env setup ───────────────────────────────────────────────────────

def block_d() -> bool:
    section("Block D — API keys & settings")
    print("Sets up .env for LLM and browser.\n")

    api_key = ask("OpenRouter API key (sk-or-...)")
    model   = ask("LLM model", "deepseek/deepseek-v3.2")
    headless = ask("Run browser headless? yes / no", "no")
    max_v   = ask("Max vacancies per session", "10")
    min_score = ask("Minimum match score to apply (0–100, skip below this)", "60")

    patches = {
        "LLM_PROVIDER": "openrouter",
        "LLM_API_KEY":  api_key,
        "LLM_MODEL":    model,
        "HEADLESS":     "true" if headless.lower().startswith("y") else "false",
        "MAX_VACANCIES": max_v,
        "MIN_SCORE":    min_score,
    }
    for k, v in patches.items():
        if v:
            _patch_env(k, v)

    print(f"\n✓  .env updated")
    return True


# ── .env patcher ──────────────────────────────────────────────────────────────

def _patch_env(key: str, value: str):
    env_path = Path(__file__).parent.parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Auto-apply agent onboarding wizard")
    parser.add_argument("--profile", type=str, default=None,
                        help="Profile name — saves to data/profiles/<name>/. Omit to be prompted "
                             "(full onboarding) or auto-selected (single-step edit, only profile).")
    parser.add_argument("--list-profiles", action="store_true",
                        help="List existing profiles and exit")
    parser.add_argument("--resume", type=Path, default=None,
                        help="Path to resume file (skips the file prompt in step 1)")
    parser.add_argument("--setup-keys", action="store_true",
                        help="Patch .env only (API key, model, headless, limits) — profile-agnostic")
    parser.add_argument("--step", type=int, choices=range(1, 8), default=None,
                        help="Run a single step of the 7-step model instead of the full wizard")
    parser.add_argument("--append", action="store_true",
                        help="Step 6: append new search URLs to existing search_urls.txt "
                             "(instead of overwriting). Use to add a new role direction later.")
    args = parser.parse_args()

    print("\n🚀 Auto-apply agent — onboarding")
    if _active_profile:
        print(f"   Profile: {_active_profile}  ({CONFIG.data_dir})")
    else:
        print("   --setup-keys only — patches the global .env, not profile-scoped.")
    print()

    steps = {
        1: lambda: step_1_resume(args.resume),
        2: step_2_identity,
        3: step_3_history,
        4: step_4_projects,
        5: step_5_skills,
        6: lambda: step_6_search_rules(append=args.append),
        7: step_7_hh_connect,
    }

    if args.setup_keys:
        block_d()
    elif args.step:
        steps[args.step]()
    else:
        # Keys first, then steps 1-7 in order. Step 1 creates candidate.json — if it
        # doesn't produce one, steps 2-6 have nothing to edit, so stop there rather
        # than run five steps that will each just print "no candidate.json yet".
        block_d()
        try:
            step1_ok = steps[1]()
        except KeyboardInterrupt:
            step1_ok = False
            print("\n⏹  Step 1 skipped")

        if not step1_ok:
            print("\n⚠  Step 1 didn't produce a candidate.json — stopping here.")
            print("   Fix the issue above (resume path / LLM_API_KEY) and re-run.")
        else:
            for n in range(2, 8):
                try:
                    steps[n]()
                except KeyboardInterrupt:
                    print(f"\n⏹  Step {n} skipped")
                    continue

    print("\n✅ Onboarding complete.")
    print("   Run: python main.py --debug --max 3")


if __name__ == "__main__":
    main()
