#!/usr/bin/env python3
"""
Onboarding wizard — run once before first main.py session.
Produces: data/resume_facts.md, data/job_preferences.md, data/tone_of_voice.md

Usage:
    python onboarding/wizard.py
    python onboarding/wizard.py --resume path/to/cv.pdf   # skip file prompt
    python onboarding/wizard.py --block a                  # run single block
"""

import argparse
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
    print(f"{prompt} (одна строка = один пункт, пустая строка — завершить):")
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


# ── Block A: Resume → resume_facts.md ─────────────────────────────────────────

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
        from onboarding.resume_parser import ResumeData as RD
        data = ResumeParser(None).from_wizard({
            "name":             ask("Full name"),
            "role":             ask("Target job title"),
            "experience_years": int(ask("Years of experience", "0") or 0) or None,
            "current_company":  ask("Current company"),
            "domain":           ask("Industry / domain (e.g. fintech, e-commerce)"),
            "skills":           ask_list("Professional skills"),
            "achievements":     ask_list("Quantified achievements (verb + metric + context)"),
            "key_cases":        ask_list("Key projects / products"),
            "tools":            ask_list("Tools (Jira, Figma, SQL, ...)"),
            "languages":        {},
        })

    out = CONFIG.data_dir / "resume_facts.md"
    parser_instance = ResumeParser(_llm_client())
    out.write_text(parser_instance.to_md(data), encoding="utf-8")
    print(f"\n✓  Saved → {out}")
    return True


# ── Block B: Job preferences → job_preferences.md + .env ─────────────────────

def block_b() -> bool:
    section("Block B — Job preferences")
    print("This shapes the search URLs and helps the agent score vacancies.")
    print("You can add multiple searches — different roles or resume directions.\n")

    from onboarding.url_builder import build_hh_url

    stop_co = ask_list("Stop-companies (companies you don't want to apply to)")
    stop_kw = ask_list("Stop-keywords in vacancy titles (e.g. junior, intern)")

    # Collect one or more search URLs
    searches = []  # list of dicts with role/city/salary/remote/url
    while True:
        print(f"\n── Search #{len(searches) + 1} ──")
        role   = ask("Target role (e.g. Product Manager)")
        city   = ask("City (e.g. Москва, remote)", "Москва")
        salary = ask("Minimum salary, RUB (press Enter to skip)")
        remote = ask("Work format: office / remote / hybrid", "hybrid")

        url = build_hh_url(role=role, city=city, salary=salary, remote=remote)
        searches.append({"role": role, "city": city, "salary": salary,
                         "remote": remote, "url": url})
        print(f"✓  URL: {url}")

        another = ask("\nAdd another search URL? yes / no", "no")
        if not another.lower().startswith("y"):
            break

    # Save search URLs (one per line)
    urls_out = CONFIG.search_urls_path
    urls_out.write_text("\n".join(s["url"] for s in searches) + "\n", encoding="utf-8")
    print(f"\n✓  {len(searches)} search URL(s) saved → {urls_out}")

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

    prefs_out = CONFIG.data_dir / "job_preferences.md"
    prefs_out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓  Preferences saved → {prefs_out}")
    return True


# ── Block C: Tone of voice → tone_of_voice.md ────────────────────────────────

def block_c() -> bool:
    section("Block C — Tone of voice")
    print("Controls how cover letters sound.\n")

    formality = ask("Style: formal / semi-formal / friendly", "semi-formal")
    length    = ask("Cover letter length: short (300-500) / medium (500-800)", "short")
    lang      = ask("Language: russian / english", "russian")
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
        f"cover_length: {length}",
        f"language: {lang}",
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
    model   = ask("LLM model", "google/gemini-2.5-flash-lite")
    headless = ask("Run browser headless? yes / no", "no")
    max_v   = ask("Max vacancies per session", "10")

    patches = {
        "LLM_PROVIDER": "openrouter",
        "LLM_API_KEY":  api_key,
        "LLM_MODEL":    model,
        "HEADLESS":     "true" if headless.lower().startswith("y") else "false",
        "MAX_VACANCIES": max_v,
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
    args = parser.parse_args()

    print("\n🚀 Auto-apply agent — onboarding")
    print("   Run once before your first session.")
    print("   All files are saved to data/ (gitignored).\n")

    blocks = {
        "a": lambda: block_a(args.resume),
        "b": block_b,
        "c": block_c,
        "d": block_d,
    }

    if args.block:
        blocks[args.block]()
    else:
        for name, fn in blocks.items():
            try:
                fn()
            except KeyboardInterrupt:
                print(f"\n⏹  Block {name.upper()} skipped")
                continue

    print("\n✅ Onboarding complete.")
    print("   Run: python main.py --debug --max 3")


if __name__ == "__main__":
    main()
