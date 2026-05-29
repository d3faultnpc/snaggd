#!/usr/bin/env python3
"""
Onboarding wizard — run once before first main.py session.
Produces: data/candidate.md, data/job_preferences.md, data/tone_of_voice.md

Usage:
    python onboarding/wizard.py
    python onboarding/wizard.py --resume path/to/cv.pdf   # skip file prompt
    python onboarding/wizard.py --block a                  # run single block
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

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


# ── Block A helpers ───────────────────────────────────────────────────────────

def _post_parse_enrich(data: ResumeData) -> ResumeData:
    """After LLM parse: fill missing personal/contact data and career self-profile.

    Personal/contacts block: runs only when something is missing.
    Career self-profile block: always runs — not extractable from CV, used by
    match scoring (role type penalty) and cover generation (context hint).
    """
    contacts = dict(data.contacts or {})
    personal = dict(data.personal or {})

    missing_personal = not data.name or not personal.get("age") or not personal.get("location")
    missing_contacts = not any(contacts.get(k) for k in ("linkedin", "github", "telegram", "email"))

    if missing_personal or missing_contacts:
        print("\n📋 A few personal details are missing — answer what you know (Enter to skip):")

        if not data.name:
            val = ask("Full name")
            if val:
                data.name = val

        if not personal.get("age"):
            val = ask("Age")
            if val:
                try:
                    personal["age"] = int(val)
                except ValueError:
                    pass

        if not personal.get("location"):
            val = ask("City / location")
            if val:
                personal["location"] = val

        if missing_contacts:
            print("\n🔗 Contact links (used to answer HR form questions like 'share your LinkedIn'):")
            for key, label in [
                ("linkedin", "LinkedIn URL"),
                ("github",   "GitHub URL"),
                ("telegram", "Telegram @handle"),
                ("email",    "Email"),
            ]:
                val = ask(f"  {label}")
                if val:
                    contacts[key] = val

        data.personal = personal
        data.contacts = contacts

    # Career self-profile — always ask, not extracted from CV.
    # role_type shapes the role-mismatch penalty in scoring.
    # edge + not_looking_for give the cover model a precise angle to write from.
    print("\n🎯 Career self-profile (shapes scoring and cover letter angle — press Enter to skip):")
    role_choices = ["builder", "operator", "strategic", "ops", "head"]
    role_val = ask(f"Role type ({'/'.join(role_choices)})", "builder")
    if role_val in role_choices:
        data.role_type = role_val

    edge_val = ask("Your edge vs other PMs in 1 sentence")
    if edge_val:
        data.professional_edge = edge_val

    not_for = ask_list("NOT looking for (e.g. process_management, pmm, outsource)")
    if not_for:
        data.not_looking_for = not_for

    return data


# ── Helpers: file patchers ────────────────────────────────────────────────────

def _patch_filters_json(data_dir: Path, *, stop_companies=None, min_employer_rating=None) -> None:
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
    if min_employer_rating is not None:
        data["min_employer_rating"] = min_employer_rating
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_salary_to_candidate(data_dir: Path, salary_text: str) -> None:
    """Append or replace §Desired Salary section in candidate.md."""
    path = data_dir / "candidate.md"
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
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
    path.write_text(content.rstrip("\n") + new_section, encoding="utf-8")


# ── Block A: Resume → candidate.md ────────────────────────────────────────────

def block_a(resume_path: Path | None = None) -> bool:
    section("Block A — Resume")
    print("Uploading your resume so the agent knows who you are.")
    print("Supported: PDF, DOCX, PNG, JPG, MD, TXT\n")

    if resume_path is None:
        raw = ask("Path to resume file (or press Enter to fill manually)").strip()
        resume_path = Path(raw) if raw else None

    data: ResumeData | None = None

    if resume_path and resume_path.exists():
        client = _llm_client()
        if client is None:
            print("⚠  LLM_API_KEY not set — cannot parse file. Switching to manual entry.")
        else:
            print(f"\nParsing {resume_path.name}...")
            try:
                parser = ResumeParser(client)
                data = parser.parse_file(resume_path)
                data = _post_parse_enrich(data)
                print(f"✓  Parsed — completeness {data.completeness:.0%}")
                if data.hints:
                    print("   Hints:")
                    for h in data.hints:
                        print(f"   · {h}")
            except Exception as e:
                print(f"⚠  Parse error: {e} — switching to manual entry.")
                data = None
    elif resume_path:
        print(f"⚠  File not found: {resume_path}")

    if data is None:
        print("\nManual entry — answer what you know, press Enter to skip.")
        def _parse_years(val: str):
            try:
                return int(val) or None
            except ValueError:
                return None

        data = ResumeParser(None).from_wizard({
            "name":             ask("Full name"),
            "role":             ask("Target job title"),
            "experience_years": _parse_years(ask("Years of experience", "0")),
            "current_company":  ask("Current company"),
            "domain":           ask("Industry / domain (e.g. fintech, e-commerce)"),
            "skills":           ask_list("Professional skills"),
            "achievements":     ask_list("Quantified achievements (verb + metric + context)"),
            "key_cases":        ask_list("Key projects / products"),
            "tools":            ask_list("Tools (Jira, Figma, SQL, ...)"),
            "languages":        {},
        })

    out = CONFIG.data_dir / "candidate.md"
    out.write_text(ResumeParser(None).to_md(data), encoding="utf-8")
    print(f"\n✓  Saved → {out}")

    if data.suggested_queries:
        sq_out = CONFIG.data_dir / "suggested_queries.txt"
        sq_out.write_text("\n".join(data.suggested_queries) + "\n", encoding="utf-8")
        print(f"✓  Search suggestions → {sq_out}")
        for q in data.suggested_queries:
            print(f"   · {q}")

    return True


# ── Block B: Job preferences → job_preferences.md + .env ─────────────────────

def block_b(append: bool = False) -> bool:
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

    # Load suggested queries from Block A (if available)
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
    searches = []
    while True:
        print(f"\n── Search #{len(searches) + 1} ──")
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
    print("   Examples: 'от 220 000 руб.' · 'default 220 000, fintech 250 000' · '200 000–350 000'")
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
        min_employer_rating=min_rating,
    )
    print(f"✓  Filters saved → {CONFIG.data_dir / 'filters.json'}")

    # Append desired salary to candidate.md
    if salary_hint:
        _append_salary_to_candidate(CONFIG.data_dir, salary_hint)
        print(f"✓  Salary added → {CONFIG.data_dir / 'candidate.md'}")

    return True


# ── Block C: Tone of voice → tone_of_voice.md ────────────────────────────────

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
    parser.add_argument("--resume", type=Path, default=None,
                        help="Path to resume file (skips the file prompt in block A)")
    parser.add_argument("--block", choices=["a", "b", "c", "d"], default=None,
                        help="Run a single block instead of the full wizard")
    parser.add_argument("--append", action="store_true",
                        help="Block B: append new search URLs to existing search_urls.txt "
                             "(instead of overwriting). Use to add a new role direction later.")
    args = parser.parse_args()

    print("\n🚀 Auto-apply agent — onboarding")
    print("   Run once before your first session.")
    print("   All files are saved to data/ (gitignored).\n")

    blocks = {
        "a": lambda: block_a(args.resume),
        "b": lambda: block_b(append=args.append),
        "c": block_c,
        "d": block_d,
    }

    if args.block:
        blocks[args.block]()
    else:
        # D first — sets LLM_API_KEY needed by Block A to parse resume
        for name in ["d", "a", "b", "c"]:
            try:
                blocks[name]()
            except KeyboardInterrupt:
                print(f"\n⏹  Block {name.upper()} skipped")
                continue

    print("\n✅ Onboarding complete.")
    print("   Run: python main.py --debug --max 3")


if __name__ == "__main__":
    main()
