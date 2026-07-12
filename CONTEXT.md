# CONTEXT.md — HH Auto-Apply Agent

> **One read = full picture.** For dev agents, contributors, and any model starting cold.
> Use the TOC to jump to the section you need by header name.
> Updated: 2026-07-12 (wizard rewritten to 7-step candidate.json model + migrate_candidate.py + OTA schema check, session 44 — uncommitted, see MEMORY.md branch state). Keep updated after major architecture changes.
> **Authority:** CONTEXT.md is the authoritative technical map. L1_project.md summarises it for session load. When they diverge, CONTEXT.md wins.

---

## Table of Contents

1. [What is this?](#1-what-is-this)
2. [System Map](#2-system-map)
3. [Data Flow](#3-data-flow)
4. [Adapter Pattern](#4-adapter-pattern)
5. [FormDetector & Handlers](#5-formdetector--handlers)
6. [LLM Integration](#6-llm-integration)
7. [Resume Parser](#7-resume-parser)
8. [Onboarding Wizard](#8-onboarding-wizard)
9. [Config & Environment](#9-config--environment)
10. [Run Modes & CLI Flags](#10-run-modes--cli-flags)
11. [Debug Mode](#11-debug-mode)
12. [File Registry](#12-file-registry)
13. [Module Contracts](#13-module-contracts)
14. [Anti-Bot Measures](#14-anti-bot-measures)
15. [Known Edge Cases & P0 Bugs](#15-known-edge-cases--p0-bugs)
16. [Key Decisions & Rejected Options](#16-key-decisions--rejected-options)
17. [Roadmap](#17-roadmap)

---

## 1. What is this?

Auto-apply agent for HH.ru job vacancies.

**User flow:** provide resume → wizard sets up profile → agent runs → finds vacancies → LLM scores each one → fills application form → submits.

**Core constraints:**
- HH.ru API closed December 2025 → Playwright-only, forever
- All HH Group sites affected: hh.ru, hh.kz, hh.by, hh.uz
- Phase 1: MIT open-source CLI tool for developers
- Phase 2+: Tauri/Electron desktop app, then SaaS

**Core DNA:** Modular + adaptable. Every site = a SiteAdapter. Every form type = a Handler. Every document format = a Parser. Adding a new job site requires zero changes to core orchestration.

---

## 2. System Map

```
main.py (orchestrator)
├── HHAdapter (adapters/hh/adapter.py)        ← SiteAdapter implementation
│   ├── HHBrowser (adapters/hh/browser.py)    ← Playwright: context, cookies, clicks, scraping
│   ├── FormDetector (adapters/hh/detector.py) ← DOM-based form classification, no LLM
│   └── FormHandlers (adapters/hh/handlers/)
│       ├── hh_modal.py     ← popup modal with cover letter textarea
│       ├── cover_only.py   ← inline cover letter form
│       ├── questions.py    ← employer Q&A (LLM batch fill)
│       ├── chat.py         ← chatik/auto-viewed redirect flow
│       ├── test_form.py    ← employer test — click skip link or fallback
│       ├── salary.py       ← skip (salary-required forms)
│       └── base.py         ← FormType enum, FormInfo, ProcessResult, BaseHandler ABC
├── LLMCover (llm_cover.py)                   ← cover letter + scoring with MD5 cache
│   └── LLMAgent (core/llm_agent.py)          ← OpenRouter gateway, system prompt cache
└── Logger (logger.py)                        ← applied_log.json + daily logs

api.py                                        ← FastAPI REST wrapper (uvicorn api:app)
                                                 Endpoints: /health, /session/start|status|stop,
                                                 /log, /config. Auth: X-API-Key header.

onboarding/
├── resume_parser.py     ← multimodal PDF/DOCX/image/md → ResumeData + ResumeData dataclass
│                           (both class and dataclass live here — no separate parsers/ dir)
├── wizard.py            ← setup CLI: Blocks A → B → C → D
└── url_builder.py       ← job prefs → HH search URL
```

**Two independent agents:**
- **Playwright agent** — all browser interaction, zero LLM tokens
- **LLM agent** (core/llm_agent.py) — cover / score / form fill / HR answers
  - System prompt (cached per session): candidate.md + job_preferences.md + tone_of_voice.md ≈ 1300 tok
  - Per vacancy user message: vacancy_text ≈ 600 tok

---

## 3. Data Flow

### One-time setup (onboarding)

```
python onboarding/wizard.py --profile <name>

  Block A → parsers/resume_parser.py → data/profiles/<name>/candidate.md
  Block B → data/profiles/<name>/job_preferences.md + data/profiles/<name>/search_urls.txt
  Block C → data/profiles/<name>/tone_of_voice.md  (optional)
  Block D → .env  (API keys, HEADLESS, MAX_VACANCIES, MIN_SCORE, ...)

python login.py → browser login → data/hh_cookies.json  (shared across profiles)
```

**Profile isolation:** each profile has its own
`candidate.md`, `applied_log.json`, `job_preferences.md`, `search_urls.txt`, `llm_cache.json`.
Cookies (`hh_cookies.json`) are shared — one HH account = one cookie file.

**Profile resolution law (2026-07-12, `profiles.py`) — same rule everywhere (CLI, wizard, API):**
there is no flat/legacy `data/` fallback in any code path. `DATA_DIR` always ends up as
`data/profiles/<name>/`:
- `--profile <name>` given → use it (must already exist for a run; created on demand by the wizard)
- omitted, exactly one profile exists → auto-selected, announced on stdout, not silent
- omitted, zero or several profiles exist → hard error naming what's missing/ambiguous; for
  brand-new onboarding (wizard, no existing profiles) the wizard prompts for a name once instead
- Rationale: an app user shouldn't need to know "attributes" exist at all — one resume ⇒ zero
  friction, several ⇒ the app/CLI must ask, never guess. See `L2_tasks.md` #24 for the app-side plan.

**Legacy flat files** (`data/*.md`, `data/*.json` at the root, pre-profiles era) — still present on
disk, no code path reads or writes there anymore since the law above landed:
- `data/candidate.md` — original PM profile (updated 2026-05-29). Historical PM
  application context; will be merged into profile log at app launch.
- `data/applied_log.json` — legacy PM log (pre-profiles). Holds PM applications before the
  profiles refactor. Source of truth for historical PM statistics.
  **Do not delete.** To be merged with `data/profiles/pm/applied_log.json` when app ships.
- `data/llm_cache.json` / `data/cover_cache.json` — deleted 2026-07-12: pure regenerable cache
  (unlike the two files above), stale since before the profiles split, no data lost.

### Runtime (main.py)

```
python main.py --profile pm        # selects data/profiles/pm/ as DATA_DIR
python main.py --list-profiles     # lists all available profiles

LLMAgent._build_system_prompt()
  ← candidate.md + job_preferences.md + tone_of_voice.md  (all from DATA_DIR)
  → cached system prompt for session

HHAdapter.verify()
  ← checks: data/hh_cookies.json exists + data/search_urls.txt non-empty

HHAdapter.start()
  → Playwright browser with injected cookies

HHAdapter.get_vacancies()
  → scrapes each URL in search_urls.txt → deduplicates by URL
  → skips URLs already in applied_log.json

per vacancy loop:
  ┌─ open vacancy page → wait 15–25s (human-like)
  ├─ get_vacancy_text() → raw text
  │
  ├─ LLMCover.generate(vacancy_text)        ← SCORING HAPPENS HERE, before any Apply click
  │    → cover_letter (string)
  │    → match_score (0–100)
  │    → matched_skills, gaps, signals
  │
  ├─ [if --dry-run] → log 'dry_run', skip to next vacancy
  ├─ [if score < MIN_SCORE] → log 'skipped_score', skip to next vacancy
  │
  ├─ click Apply button
  ├─ [if immediate success notification] → log 'applied_immediate', done
  │
  ├─ FormDetector.detect(page) → form_type
  ├─ [if SALARY_FORM or UNKNOWN] → log 'skipped_*', done
  │
  ├─ handler = FormHandlers.get_handler(form_type)
  ├─ result = handler.process(page, cover_letter, vacancy_text=vacancy_text, **kwargs)
  └─ Logger.log_result() → <DATA_DIR>/applied_log.json
```

**Active applied logs:**
- `data/profiles/pm/applied_log.json` — current PM profile (post-profiles refactor)
- `data/profiles/support/applied_log.json` — support profile log
- `data/applied_log.json` — LEGACY pre-profiles PM log (historical, do not delete)

**Why scoring before Apply click:** HH tracks Apply button clicks. Clicking without submitting leaves a trace. Score first → click only if score passes and it's not a dry-run.

---

## 4. Adapter Pattern

```python
class SiteAdapter(ABC):
    def name(self) -> str           # "hh.ru"
    def auth_method(self) -> str    # "cookie" | "oauth" | "form"
    def verify(self) -> bool        # pre-flight: cookies + URLs configured
    def start(self) -> bool         # launch browser / initialize session
    def close(self) -> None
    def get_vacancies(self) -> list # [(url, title, index), ...]
    def process_vacancy(url, title, index, llm_cover, **kwargs) -> dict
```

Current: `adapters/hh/` — Playwright + cookie injection.
Phase 3 targets: `adapters/greenhouse/` (API), `adapters/lever/` (API), `adapters/workday/` (Playwright).

---

## 5. FormDetector & Handlers

### Detection Priority (detector.py)

`IMMEDIATE` is caught in adapter.py before FormDetector runs (no form = instant success notification).
Everything below is FormDetector._classify_form():

```
Priority | Signal                                                  | FormType
---------|-----------------------------------------------------------|-----------------
  0e     | role=dialog overlay with visible textarea               | → HH_MODAL_STEP1  (cover-required gate)
  0a     | has_test_form + has_task_questions                      | → EMPLOYER_QUESTIONS
  0a     | has_test_form only                                      | → TEST_FORM
  0c     | has_popup_questions or has_task_questions               | → EMPLOYER_QUESTIONS
  0b     | has_form_error + has_chat_link (auto-read/Sber pattern) | → CHAT_INTERFACE
  0d     | has_response_submit (post-apply cover letter flow)      | → COVER_ONLY
  1      | has_progress or hh_modal/navigation keywords            | → HH_MODAL_STEP1 / HH_MODAL_STEP2
  2      | has_salary_field                                        | → SALARY_FORM (skip)
  3      | has_chat_link (verified data-qa only, not button text)  | → CHAT_INTERFACE
  4      | input_count > 1 or questions keywords                   | → EMPLOYER_QUESTIONS
  5      | input_count == 1 + cover field or cover keywords        | → COVER_ONLY
  6      | input_count == 1 (fallback)                             | → COVER_ONLY
  —      | nothing matched                                         | → UNKNOWN (skip)
```

**FormType enum values:** `hh_modal_step1`, `hh_modal_step2`, `cover_only`, `employer_questions`,
`salary_form`, `chat_interface`, `test_form`, `unknown`

**HH_MODAL_STEP1 vs STEP2:** STEP1 = first modal (cover letter entry). STEP2 = progress > 1, a subsequent modal step in a multi-step HH application flow.

### Handler Behaviors

| Handler | What it does | Selectors used |
|---------|-------------|----------------|
| `hh_modal.py` | Multi-step: fills cover in modal → waits for submit to enable → submits. `delay=5`, `timeout=60000`. | `vacancy-response-submit-popup`, `vacancy-response-popup-form-letter-input` |
| `cover_only.py` | Fills inline cover textarea → submits | `vacancy-response-letter-submit`, `vacancy-response-letter-informer` |
| `questions.py` | Extracts text/radio/checkbox fields → LLM batch fill → submits. Radio: grouped by `name`. Checkbox: `el.check()` per yes/no LLM answer. | `vacancy-response-question`, `add-cover-letter` |
| `test_form.py` | Clicks "apply without questions" link → fills cover → submits | `vacancy-response-link-no-questions` |
| `chat.py` | Clicks chat link → types cover in chatik input | `vacancy-response-link-view-topic`, `chatik-new-message-text` |
| `salary.py` | Always skips | — |

### Key Selector Rules

- `vacancy-response-submit-popup` always starts **disabled** — must wait `':not([disabled])'` after typing
- `form-helper-error` BEFORE submit = CHAT_INTERFACE signal (not a form error)
- `form-helper-error` AFTER submit = real validation error (check error text)
- Never use `.fill()` — HH forms use React, `.fill()` doesn't fire `onChange`. Always `.type(text, delay=10)`.

---

## 6. LLM Integration

**Gateway:** OpenRouter (configured via `OPENROUTER_API_KEY` + `LLM_MODEL` in .env)
**Default model:** `deepseek/deepseek-v3.2` — override via `LLM_MODEL` env var. Cover letter model: `COVER_MODEL` env var (defaults to `LLM_MODEL`).
**BYOK:** User brings their own OpenRouter key. Phase 4: managed keys with margin.

### System Prompt (cached per session)

Built by `LLMAgent._build_system_prompt()` from three files in `DATA_DIR`:
```
candidate.md          → "CANDIDATE PROFILE" section
job_preferences.md    → "JOB PREFERENCES" section
tone_of_voice.md      → "TONE & STYLE" section  (optional)
Total: ≈ 1300 tokens
```
`DATA_DIR` = `data/profiles/<name>/`, always — see the profile resolution law in §3.

### Per-Vacancy Calls

1. `LLMCover.generate(vacancy_text)` → calls two LLM endpoints:
   - `LLMAgent.score_vacancy(text)` → `{score: 0–100, matched_skills: [], gaps: [], signals: []}`
   - `LLMAgent.generate_cover(text)` → cover letter string, same language as vacancy

2. `QuestionsHandler` (EMPLOYER_QUESTIONS form type):
   - Collects ALL visible fields with labels in one pass
   - Cover-letter fields: pre-filled with the pre-generated cover letter directly
   - Remaining fields: one batch call `LLMAgent.fill_form(vacancy_text, fields)` → `{idx: answer}`
   - Prompt: `prompts/form_fill.md`. LLM uses candidate profile to answer in vacancy language.
   - No file-based Q&A bank. No per-question LLM calls.

### Language Detection

Cover letters are generated in the same language as the vacancy. Instruction in `prompts/cover_letter.md`:
`"Respond in the SAME LANGUAGE as the vacancy text (Russian vacancy → Russian letter, English vacancy → English letter)"`

### Cache

Two separate caches in `DATA_DIR`:
- `llm_cache.json` — score cache, keyed by MD5(vacancy_text + model + candidate_hash)
- `cover_cache.json` — cover letter cache, keyed by vacancy_id (duplicates always get fresh cover at temp>0)

---

## 7. Resume Parser

**File:** `onboarding/resume_parser.py` — both `ResumeParser` class and `ResumeData` dataclass live here.
**Formats:** PDF (multimodal LLM, no local lib), DOCX (python-docx), PNG/JPG (multimodal), .md
**Output:** `<DATA_DIR>/candidate.md` (rendered text, system prompt, wrapped in
  `<!-- snaggd:start/end -->` managed-block markers — content after the end marker is a user's own
  free-text and survives re-renders) + `<DATA_DIR>/candidate.json` (structured source of truth,
  written by wizard Step 1; `ResumeData` mirrors this shape 1:1 via `dataclasses.asdict()`).
  Schema rewritten session 42 (2026-07-11): nested `identity/career_profile/logistics/search/
  rules/cases[]` shape, replaces old flat fields (`jobs`/`side_projects`/`contacts: dict`/etc).
  Rendering fixes session 44 (2026-07-12): `case["url"]` now actually renders (was extracted but
  silently dropped); bare domains (`github.com/x`) get `https://` prepended via `_ensure_https()`
  so they auto-link in standard MD viewers; the `# completeness: X% | source: Y | updated: Z`
  header line was removed entirely — pure metadata already in `candidate.json`'s own fields,
  no prompt uses it, cost tokens for zero function.
**Prompt:** inline in `_extraction_prompt()` — no `prompts/resume_parser.md` file.
**Smoke test:** `python scripts/sanity_parser.py`

**Legacy migration (`scripts/migrate_candidate.py`, session 44):** one-time converter for
profiles whose `candidate.md` predates this schema. Two independent reads that must stay
independent (see the script's own docstring): LLM facts-extraction (same pipeline as Step 1,
with wizard-owned sections stripped from the input text first) for CV content; deterministic
`## Header`-parsing for wizard-authored preference sections (`career_profile`/`logistics`/
`search.salary`/`rules.penalize` — never resume-derived, even in the old format). Not every
legacy profile has both halves — some old `candidate.md` files are closer to a raw resume than
wizard output; the script detects and says so rather than guessing. Safe by default: writes to
`candidate.migrated.{json,md}` only, never the live files; `--apply` + an explicit `y` at a
confirmation prompt (if the live file already exists) required to promote.

> For full ResumeData schema and edge cases: `memory/domain/resume_parser.md` (updated session 42)
> Full schema spec + rationale: `.claude/working-notes/tz-pre-app-wizard-sprint.md`

---

## 8. Onboarding Wizard

**File:** `onboarding/wizard.py`
**Run:** `python onboarding/wizard.py --profile <name>` (full onboarding, steps 1-7 in order) or
`--step N` (single step, 1-7) or `--setup-keys` (`.env` only, profile-agnostic)

Rewritten session 44 (2026-07-12) from a 4-block CLI model (Block A/B/C/D) to a 7-step,
`candidate.json`-first model. The old blocks are gone from the CLI surface entirely — a clean
cut, not a compat shim: `block_a`/its helpers were deleted outright once steps 1-6 replaced
them; `block_b`/`block_c`/`block_d` survive only as internal helpers steps 5/6 and `--setup-keys`
still call, no longer independently reachable from the CLI.

| Step | What it does | Writes |
|------|--------------|--------|
| 1. Resume | LLM parse only, no Q&A (that's steps 2/5/6) — cases/skills/tools/languages/interests/hints/target_market/locale/identity/pitch, always freshly overwritten on re-run | `candidate.md` + `candidate.json` |
| 2. Identity | Review/edit `identity.*` + `pitch` — shows current values as defaults, Enter keeps them | `candidate.json` |
| 3. History | Review/edit employment + education cases — edit existing by number or `new` to add | `candidate.json` |
| 4. Projects | Same case-review UI as Step 3, filtered to `project`/`certification`/`publication`/`volunteering`/`research` types — the split mirrors `resume_parser.py`'s own render-time bucketing exactly (`_PROJECT_TYPES`), so wizard-side and render-side classification can't drift apart | `candidate.json` |
| 5. Skills | `skills[]`/`tools[]`/`languages[]` + `career_profile` (`role_type`/`edge`/`aspiration`), optional tone-of-voice tail | `candidate.json` (+ `tone_of_voice.md` if opted in) |
| 6. Search & Rules | Wraps the pre-existing job-prefs flow unchanged (stop lists, wise-link auto-detect, search directions, salary) — writes the same real files it always did, then additionally dual-writes `search`/`rules`/`logistics` into `candidate.json` | `job_preferences.md` + `search_urls.txt` + `filters.json` + `candidate.md` (salary patch) + `candidate.json` |
| 7. HH Connect | Subprocess wrapper around `login.py` (unmodified) — asks for confirmation first, verifies `hh_resumes.json` actually has entries afterward rather than trusting `login.py`'s exit code alone (that code only reflects the cookie-save phase) | `data/hh_cookies.json` + `data/hh_resumes.json` (shared across profiles, not written to the profile dir) |

**Design principles:**
- Steps 2-7 all require Step 1 to have already produced a `candidate.json` (`_require_candidate()`
  fails cleanly with a message otherwise, no traceback).
- `career_profile`/`logistics`/`search`/`rules` are wizard-filled ONLY — Step 1's LLM pass never
  writes them; re-running Step 1 preserves them verbatim from the existing `candidate.json`.
- No manual-entry fallback in Step 1 (unlike the old Block A) — Step 1 is LLM-only by design;
  manual field entry now lives in steps 2-6's own review/edit flow instead.
- Full onboarding with no flags: `--setup-keys` (was Block D) runs first, then steps 1-7 in
  order; stops after Step 1 if it didn't produce a `candidate.json` rather than running five
  steps that would each just report nothing to edit.

**Known open items (not bugs, deliberate follow-ups):**
- Step 6's `rules.stop` / `rules.min_employer_rating` / `logistics.*` are additive-only —
  `adapter.py` still reads `filters.json` / `job_preferences.md` directly (§9), not
  `candidate.json.rules.*`. Wiring that up is unscheduled.
- `rules.min_match` (per-profile `MIN_SCORE` override) is collected but not read by anything —
  `MIN_SCORE` is still a global env var at runtime.
- Step 6's wise-link auto-detect needs `hh_resumes.json`, which Step 7 produces — running the
  full 1-7 sequence for a brand-new profile means Step 6 won't have auto-detect data yet on
  a first pass.

**URL Builder (`onboarding/url_builder.py`):**
Builds HH search URLs from job prefs. Supports 6 cities. Key param: `search_field=name` (title only) — pending change to `everywhere` (task #2).

**Critical:** `data/search_urls.txt` must contain `/search/vacancy?text=...` URLs for
keyword-based searches. One deliberate exception: the auto-detected wise link
(`_pick_auto_wise_link()`, Step 6) uses `?resume=<uuid>&from=resumelist` on purpose — that's
the correct shape for a resume-tied link, not the generic recommendation-feed problem this
rule exists to prevent.

---

## 9. Config & Environment

**File:** `config.py` — single source of truth for all config, selectors, keywords.

```bash
# .env (gitignored, created by wizard Block D)
OPENROUTER_API_KEY=sk-or-...    # required — OpenRouter key
LLM_MODEL=deepseek/deepseek-v3.2  # scoring + form fill model
COVER_MODEL=                    # optional override for cover letter generation (defaults to LLM_MODEL)
HEADLESS=false                  # false = visible browser (recommended for debugging)
MAX_VACANCIES=10                # max vacancies to process per run
MIN_SCORE=60                    # skip vacancies below this score (0–100)
VACANCIES_PER_URL=10            # max vacancies collected per search URL per run (even rotation)
DATA_DIR=./data                 # low-level override; normally set automatically by profiles.py
PROXY_URL=                      # socks5://... for users who need proxy
BROWSER_CORNER=false            # true → 750×430 window at bottom-right corner (work alongside)
BROWSER_CORNER_X=1578           # corner window X position (default: bottom-right of 2560px screen)
BROWSER_CORNER_Y=650            # corner window Y position
```

**Config object** (`config.py`):
```python
CONFIG.cookies_path        → data/hh_cookies.json  (shared; not inside profile dir)
CONFIG.search_urls_path    → <DATA_DIR>/search_urls.txt
CONFIG.applied_log_path    → <DATA_DIR>/applied_log.json
CONFIG.min_score           → int (default 60)

`DATA_DIR` env var: set by `profiles.py`'s `resolve_profile()` before `config` is first imported
(main.py, wizard.py) or passed as `data_dir=` directly (api.py) — always a real profile path, see §3.
```

**SELECTORS dict** — all Playwright selectors live here, not in handler files.
**FORM_KEYWORDS dict** — navigation button text patterns for fallback matching.

**OTA schema check (session 44, Task 8):** right after `CONFIG = Config()`, `config.py` calls
`_check_candidate_schema(CONFIG.data_dir)` once per process start. Advisory only — never
raises, never exits, never writes. Prints a note if `candidate.json` is missing (but
`candidate.md` exists — a pre-schema profile, points at the exact `migrate_candidate.py`
command) or if its `schema_version` doesn't match `CURRENT_SCHEMA_VERSION` ("1.0" today, no
migration path exists yet since no v2 has ever shipped). Silent for a brand-new profile
(neither file yet) and for the flat/legacy dir (`--setup-keys`, no active profile — guarded by
`data_dir.parent.name != "profiles"`). Deliberately does NOT auto-run the migration script —
`candidate.json` isn't read by the live apply loop yet (system prompt still comes from
`candidate.md` directly, see §6), so an absent/stale file can't break a running session; auto-
migrating would mean a silent LLM call + disk write on every startup, which is exactly what
`migrate_candidate.py`'s own `--apply` gate exists to prevent.

---

## 10. Run Modes & CLI Flags

```bash
# Full run
python main.py

# Score vacancies without applying (no Apply click, no HH trace)
python main.py --dry-run

# Limit vacancies processed
python main.py --max 5

# Debug mode: screenshots + HTML + data-qa list per vacancy
python main.py --debug

# Combine
python main.py --dry-run --debug --max 3

# Sandbox (isolated data directory, safe for testing)
DATA_DIR=sandbox/data python main.py --debug --max 1

# Single wizard step (1-7, see §8)
python onboarding/wizard.py --profile pm --step 6

# Legacy candidate.md -> candidate.json migration (dry-run by default, see §7/§8)
python scripts/migrate_candidate.py --profile pm
```

---

## 11. Debug Mode

`--debug` → `HHAdapter._debug_snapshot()` saves per-vacancy to `snapshots/{timestamp}/`:
- `01_vacancy_page.{png,html,_data_qa.txt}` — before Apply click
- `02_after_apply_click.*` — form/popup visible
- `03_after_handler_{status}.*` — after handler
- `error.*` — on unexpected exception

---

## 12. File Registry

### Active profile files (post-profiles refactor, session ~30+)

Each profile lives in `data/profiles/<name>/`. Created by `wizard.py --profile <name>`.

| File | Created by | Notes |
|------|-----------|-------|
| `data/profiles/<name>/candidate.md` | wizard Step 1 | candidate profile for LLM system prompt |
| `data/profiles/<name>/candidate.json` | wizard Step 1 (+ steps 2-6 edit it) | structured source of truth, session 44. Not yet read by the live apply loop (§9) |
| `data/profiles/<name>/job_preferences.md` | wizard Step 6 | role, city, salary, stop filters |
| `data/profiles/<name>/search_urls.txt` | wizard Step 6 | HH search URLs for this profile |
| `data/profiles/<name>/tone_of_voice.md` | wizard Step 5 (optional tail) | cover letter tone (optional) |
| `data/profiles/<name>/applied_log.json` | runtime (logger.py) | per-profile application log |
| `data/profiles/<name>/llm_cache.json` | runtime (llm_cover.py) | MD5 cache per profile |
| `data/profiles/<name>/cover_cache.json` | runtime (llm_cover.py) | cover letter cache keyed by vacancy_id |
| `data/hh_cookies.json` | login.py (via wizard Step 7) | **shared** across profiles (one HH account) |
| `data/hh_resumes.json` | login.py (via wizard Step 7) | [{title, uuid}] for all HH resumes; used by wizard Step 6 for auto-wise-link |

As of session 44: neither `pm` nor `support` has a real `candidate.json` yet (only `candidate.md`,
pre-schema format) — migration to the new schema is a deliberate, explicit, user-run action
(`scripts/migrate_candidate.py`), not automatic. See §7/§8/§9.

Current profiles: `pm`, `support`.

### Legacy flat files (pre-profiles, data/ root)

**Do not delete** — contain historical data to be merged at app launch.

| File | Status | Notes |
|------|--------|-------|
| `data/candidate.md` | Legacy PM profile (updated 2026-05-29) | Historical PM candidate context. Will be merged. |
| `data/applied_log.json` | Legacy PM log (pre-profiles) | PM applications before profiles refactor. Merge target at app launch with `data/profiles/pm/applied_log.json` for unified statistics. |
| `data/applied_log.json.bak` | Backup | Keep. |
| `data/resume_facts.md` | Legacy LLM-parsed output | Pre-profiles. Superseded by `profiles/pm/candidate.md`. |
| `data/job_preferences.md` | Legacy PM prefs | Pre-profiles. |

### Code files (committed)

| File | Created by | Gitignored |
|------|-----------|------------|
| `config.py` | code | no |
| `profiles.py` | code | no — profile discovery + resolution law, shared by main/wizard/api |
| `prompts/cover_letter.md` | code | no |
| `prompts/match_scoring.md` | code | no |
| `prompts/form_fill.md` | code | no |
| `docs/status_codes.md` | code | no |
| `docs/phase2-prompts/cv_extractor.md` | code | no — Phase 2 design artifact |
| `docs/phase2-prompts/resume_enhancer.md` | code | no — Phase 2 design artifact |
| `api.py` | code | no |
| `tests/test_score_clamp.py` | session 40 | unit tests for score clamping (9 cases, no LLM) |
| `tests/test_browser_close.py` | session 40 | unit tests for HHBrowser.close() idempotency (3 cases) |
| `scripts/migrate_candidate.py` | session 44 | legacy `candidate.md` → `candidate.json`; see §7 |

> `data/` is created by wizard. Never committed. One folder per user installation.

---

## 13. Module Contracts

### profiles.resolve_profile(requested, *, exit_on_error=True) → profile_name
- No requested name: 1 profile → auto-select (printed); 0 or 2+ → error listing what exists
- Requested name given: must exist, else error listing available profiles
- `exit_on_error=True` (main.py, wizard.py — single-shot CLI process): prints and `sys.exit(1)`
- `exit_on_error=False` (api.py — long-lived server process): raises `ProfileError` instead
- Import-light on purpose (no `config` dependency) — main.py/wizard.py call it before `config` is
  first imported, since `DATA_DIR` must already be in `os.environ` by then
- Never resolves to a flat/legacy `data/` dir — see the resolution law in §3

### LLMCover.generate(vacancy_text) → (cover_letter, template_name, signals)
- Score cache: MD5(vacancy_text) → `<DATA_DIR>/llm_cache.json`
- Cover cache: vacancy_id → `<DATA_DIR>/cover_cache.json` (separated — duplicates always get fresh cover)
- LLM unavailable → `skipped_llm_unavailable`, result NOT cached, retryable next run
- After call: `self.last_score`, `self.last_matched_skills`, `self.last_gaps` set

### FormDetector.detect(page) → FormInfo
- DOM only, no LLM, must complete < 1s
- Returns `FormInfo(form_type, input_count, has_salary_field)`

### BaseHandler.process(page, cover_letter, **kwargs) → ProcessResult
- All handlers accept `**kwargs` (including `vacancy_text` for QuestionsHandler)
- Returns `ProcessResult(success, status, reason, scenario, details)`

### applied_log.json entry schema

**Vacancy entry:**
```json
{
  "date": "ISO8601",
  "url": "string",
  "title": "string",
  "vacancy_id": "string",
  "status": "applied | applied_immediate | applied_via_chat | applied_no_cover | dry_run | skipped_score | skipped_llm_unavailable | skipped_* | ...",
  "form_type": "hh_modal_step1 | hh_modal_step2 | cover_only | employer_questions | chat_interface | test_form | immediate | unknown",
  "goal_reached": true,
  "match_score": 75,
  "matched_skills": ["skill1"],
  "gaps": ["missing_skill"],
  "signals": ["platform", "b2b"],
  "search_source": "wise_link | <query text>",
  "template_name": "llm",
  "company": "ООО Example",
  "employer_rating": 4.2,
  "cover_length": 612,
  "duplicate_of": "123456"
}
```
`duplicate_of` only present on duplicate vacancies (same desc, different URL). `employer_rating` is `null` for companies with no reviews.
`skipped_llm_unavailable` is retryable — same URL will be processed again next run.

**Session-end entry** (appended at run end):
```json
{
  "type": "session_end",
  "date": "ISO8601",
  "reason": "max_vacancies_reached | completed | error",
  "detail": "string",
  "processed": 20
}
```

---

## 14. Anti-Bot Measures

`random_delay(15000, 25000)` after page open · `.type(text, delay=10)` everywhere (no `.fill()` — React) · cookies injection, no programmatic login · no HH API calls · `PROXY_URL` for socks5.

---

## 15. Known Edge Cases

| # | Case | File | Status |
|---|------|------|--------|
| 1 | QuestionsHandler._submit() — submit after Q&A fill not verified live | `handlers/questions.py` | open |
| 2 | Chat selectors (chatik-* data-qa) | `handlers/chat.py` | partially verified (#8 done; selectors may drift) |
| 3 | "Application already viewed" modal popup | `handlers/hh_modal.py` | not yet encountered live |

---

## 16. Key Decisions

> Full decision log: `memory/L2_decisions_log.md`

- **Playwright, not HH API** — API closed Dec 2025, Playwright-only forever.
- **OpenRouter BYOK** — vendor-agnostic; user brings key. Phase 4: managed keys.
- **Score BEFORE Apply click** — HH tracks clicks; scoring first enables zero-side-effect dry-run.
- **`.type(delay=10)`, never `.fill()`** — `.fill()` skips React `onChange`; submit stays disabled.

---

## 17. Roadmap

| Phase | What | Status |
|-------|------|--------|
| 1 — MIT GitHub | HH.ru adapter + wizard + CLI | **In progress (PHASE1-NEXT)** |
| 2 — Desktop | Tauri/Electron + FastAPI + Supabase | Design in progress (Claude Design) |
| 3 — Multisite | Greenhouse/Lever (API) → Workday (Playwright) → LinkedIn (Patchright) | Planned |
| 4 — SaaS | Managed LLM, subscriptions, multi-tenant | Future |

> Open Phase 1 tasks + master plan: `memory/L2_tasks.md`
> Phase 3 priority: Greenhouse + Lever first (API-native). Skip: Indeed (DataDome), Zarplata.ru (HH redirect).
