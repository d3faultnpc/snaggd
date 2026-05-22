# CONTEXT.md — HH Auto-Apply Agent

> **One read = full picture.** For dev agents, contributors, and any model starting cold.
> Use the TOC to jump to the section you need by header name.
> Updated: 2026-05-22 (session 7). Keep updated after major architecture changes.

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
├── HRMatcher (hr_matcher.py)                 ← HR question answering via LLMAgent
└── Logger (logger.py)                        ← applied_log.json + daily logs

parsers/
├── resume_parser.py     ← multimodal PDF/DOCX/image/md → ResumeData
└── resume_data.py       ← ResumeData dataclass

onboarding/
├── wizard.py            ← setup CLI: Blocks A → B → C → D
└── url_builder.py       ← job prefs → HH search URL
```

**Two independent agents:**
- **Playwright agent** — all browser interaction, zero LLM tokens
- **LLM agent** (core/llm_agent.py) — cover / score / form fill / HR answers
  - System prompt (cached per session): resume_facts + job_preferences + tone_of_voice ≈ 1300 tok
  - Per vacancy user message: vacancy_text ≈ 600 tok

---

## 3. Data Flow

### One-time setup (onboarding)

```
python onboarding/wizard.py

  Block A → parsers/resume_parser.py → data/resume_facts.md
  Block B → data/job_preferences.md + data/search_urls.txt
  Block C → data/tone_of_voice.md
  Block D → .env  (API keys, HEADLESS, MAX_VACANCIES, MIN_SCORE, ...)

python login.py → browser login → data/hh_cookies.json
```

### Runtime (main.py)

```
LLMAgent._build_system_prompt()
  ← resume_facts.md + job_preferences.md + tone_of_voice.md
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
  ├─ result = handler.process(page, cover_letter, hr_matcher, vacancy_text=vacancy_text)
  └─ Logger.log_result() → data/applied_log.json
```

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
    def process_vacancy(url, title, index, llm_cover, hr_matcher,
                        debug, session_dir, dry_run) -> dict
```

Current: `adapters/hh/` — Playwright + cookie injection.
Phase 3 targets: `adapters/greenhouse/` (API), `adapters/lever/` (API), `adapters/workday/` (Playwright).

---

## 5. FormDetector & Handlers

### Detection Priority (detector.py)

```
Priority | Signal                                              | FormType
---------|-----------------------------------------------------|------------------
  0a     | applied_immediate notification visible              | → IMMEDIATE
  0b     | form-helper-error + vacancy-response-link-view-topic visible | → CHAT_INTERFACE
  0c     | vacancy-response-question elements in popup         | → EMPLOYER_QUESTIONS
  1      | standard popup with textarea                        | → HH_MODAL
  2      | inline textarea                                     | → COVER_ONLY
  3      | employer-asking-for-test marker                     | → TEST_FORM
  4      | salary input field visible                          | → SALARY_FORM (skip)
  5      | nothing matched                                     | → UNKNOWN (skip)
```

### Handler Behaviors

| Handler | What it does | Selectors used |
|---------|-------------|----------------|
| `hh_modal.py` | Fills cover textarea in popup → waits for submit to enable → submits | `vacancy-response-submit-popup`, `vacancy-response-popup-form-letter-input` |
| `cover_only.py` | Fills inline textarea → submits | `vacancy-response-letter-submit`, `vacancy-response-letter-informer` |
| `questions.py` | Extracts Q&A fields → LLM batch fill → submits | `vacancy-response-question`, `add-cover-letter` |
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

**Gateway:** OpenRouter (configured via `LLM_API_KEY` + `LLM_MODEL` in .env)
**Default model:** `google/gemini-2.5-flash-lite` (fast, cheap) — override via `LLM_MODEL` env var
**BYOK:** User brings their own OpenRouter key. Phase 4: managed keys with margin.

### System Prompt (cached per session)

Built by `LLMAgent._build_system_prompt()` from three files:
```
data/resume_facts.md      → "CANDIDATE PROFILE" section
data/job_preferences.md   → "JOB PREFERENCES" section
data/tone_of_voice.md     → "TONE & STYLE" section
Total: ≈ 1300 tokens
```

### Per-Vacancy Calls

1. `LLMCover.generate(vacancy_text)` → calls two LLM endpoints:
   - `LLMAgent.score_vacancy(text)` → `{score: 0–100, matched_skills: [], gaps: [], signals: []}`
   - `LLMAgent.generate_cover(text)` → cover letter string, same language as vacancy

2. `HRMatcher.answer(question, context)` → answer string (EMPLOYER_QUESTIONS handler only)
   - Primary: LLMAgent
   - Optional context: `data/hr_questions.md` if present

### Language Detection

Cover letters are generated in the same language as the vacancy. Instruction in `prompts/cover_letter.md`:
`"Respond in the SAME LANGUAGE as the vacancy text (Russian vacancy → Russian letter, English vacancy → English letter)"`

### Cache

`LLMCover` caches responses by MD5 hash of vacancy_text in `data/llm_cache.json`.
Static fallback if LLM unavailable: `"Добрый день. Заинтересован..."` (minimal cover letter).

---

## 7. Resume Parser

**File:** `parsers/resume_parser.py`
**Supported formats:** PDF, DOCX, PNG/JPG (multimodal), .md

**Flow:**
```
input file → detect format → extract content (text or image bytes)
          → LLM call (with prompts/resume_parser.md)
          → parse JSON → ResumeData dataclass
          → write data/resume_facts.md
```

**ResumeData fields:**
```python
name: str
target_role: str
skills: List[str]
experience: List[dict]   # {company, role, duration, description}
education: List[dict]
languages: List[str]
summary: str
suggested_queries: List[str]  # [PENDING task #1] LLM-generated HH search queries
```

**Edge cases:**
- Scanned PDFs (no text) → falls back to image rendering → multimodal LLM
- DOCX tables → python-docx may miss them → LLM recovers from partial text

**Smoke test:** `python scripts/sanity_parser.py`

---

## 8. Onboarding Wizard

**File:** `onboarding/wizard.py`
**Run:** `python onboarding/wizard.py` (full) or `--block a/b/c/d` (single block)

| Block | What it does | Output |
|-------|-------------|--------|
| A | Parse resume (PDF/DOCX/image) → ResumeData | `data/resume_facts.md` |
| B | Job preferences: role, city, schedule, salary | `data/job_preferences.md` + `data/search_urls.txt` |
| C | Tone of voice for cover letters | `data/tone_of_voice.md` |
| D | LLM API key, model, headless mode, limits | `.env` patches |

**URL Builder (`onboarding/url_builder.py`):**
Builds HH search URLs from job prefs. Supports 6 cities. Key param: `search_field=name` (title only) — pending change to `everywhere` (task #2).

**Critical:** `data/search_urls.txt` must contain `/search/vacancy?text=...` URLs. Never use `from=resumelist` URLs — those are HH's recommendation feed, not search results.

---

## 9. Config & Environment

**File:** `config.py` — single source of truth for all config, selectors, keywords.

```bash
# .env (gitignored, created by wizard Block D)
OPENROUTER_API_KEY=sk-or-...    # required — OpenRouter key
LLM_MODEL=google/gemini-2.5-flash-lite  # override for different model
HEADLESS=false                  # false = visible browser (recommended for debugging)
MAX_VACANCIES=10                # max vacancies to process per run
MIN_SCORE=60                    # skip vacancies below this score (0–100)
DATA_DIR=./data                 # override data directory location
PROXY_URL=                      # socks5://... for users who need proxy
```

**Config object** (`config.py`):
```python
CONFIG.cookies_path        → data/hh_cookies.json
CONFIG.search_urls_path    → data/search_urls.txt
CONFIG.applied_log_path    → data/applied_log.json
CONFIG.min_score           → int (default 60)
```

**SELECTORS dict** — all Playwright selectors live here, not in handler files.
**FORM_KEYWORDS dict** — navigation button text patterns for fallback matching.

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

# Single wizard block
python onboarding/wizard.py --block b
```

---

## 11. Debug Mode

When `--debug` is passed, `HHAdapter._debug_snapshot(page, session_dir, label)` is called at key pipeline steps.

**Output per vacancy:** `snapshots/{timestamp}/`
- `{label}.png` — full-page screenshot
- `{label}.html` — modal HTML (or full body if no modal)
- `{label}_data_qa.txt` — all `data-qa` attribute values on the page

**Labels:**
- `01_vacancy_page` — before Apply click
- `02_after_apply_click` — form/popup visible
- `03_after_handler_{status}` — after handler finishes
- `error` — on unexpected exception

---

## 12. File Registry

| File | Created by | Gitignored |
|------|-----------|------------|
| `data/resume_facts.md` | wizard Block A | yes |
| `data/job_preferences.md` | wizard Block B | yes |
| `data/search_urls.txt` | wizard Block B | yes |
| `data/tone_of_voice.md` | wizard Block C | yes |
| `data/hh_cookies.json` | login.py | yes |
| `data/applied_log.json` | runtime (logger.py) | yes |
| `data/llm_cache.json` | runtime (llm_cover.py) | yes |
| `data/hr_questions.md` | user-created, optional | yes |
| `config.py` | code | no |
| `prompts/cover_letter.md` | code | no |
| `prompts/match_scoring.md` | code | no |
| `prompts/form_fill.md` | code | no |
| `prompts/resume_parser.md` | code | no |

> `data/` is created by wizard. Never committed. One folder per user installation.

---

## 13. Module Contracts

### LLMCover.generate(vacancy_text) → (cover_letter, template_name, signals)
- MD5 cache on vacancy_text → `data/llm_cache.json`
- Static fallback if LLM unavailable
- After call: `self.last_score`, `self.last_matched_skills`, `self.last_gaps` set

### FormDetector.detect(page) → FormInfo
- DOM only, no LLM, must complete < 1s
- Returns `FormInfo(form_type, input_count, has_salary_field)`

### BaseHandler.process(page, cover_letter, hr_matcher, **kwargs) → ProcessResult
- All handlers accept `**kwargs` (including `vacancy_text` for QuestionsHandler)
- Returns `ProcessResult(success, status, reason, scenario, details)`

### applied_log.json entry schema
```json
{
  "url": "string",
  "title": "string",
  "date": "ISO8601",
  "status": "applied | applied_immediate | skipped_score | dry_run | skipped_* | ...",
  "form_type": "hh_modal | immediate | questions | chat | test_form | ...",
  "match_score": 75,
  "matched_skills": ["skill1"],
  "gaps": ["missing_skill"],
  "signals": ["platform", "b2b"],
  "cover_sent": true,
  "template_name": "cover_letter_v1"
}
```

---

## 14. Anti-Bot Measures

- **Human-like delays:** `random_delay(15000, 25000)` after opening vacancy (15–25 seconds)
- **Typing delay:** `textarea.type(text, delay=10)` — 10ms per character
- **No `.fill()`** — fires events like a real keyboard
- **Cookies injection:** Playwright loads saved cookies, no programmatic login
- **No API calls to HH** — pure browser automation
- **Proxy support:** `PROXY_URL` env var for socks5 proxy

---

## 15. Known Edge Cases & P0 Bugs

| # | Case | File | Status |
|---|------|------|--------|
| 1 | QuestionsHandler._submit() — submit after Q&A fill | `handlers/questions.py` | **NOT tested live** |
| 2 | Chat selectors (chatik-* data-qa) | `handlers/chat.py` | **NOT verified live** |
| 3 | "Application already viewed" modal popup | `handlers/hh_modal.py` | Not encountered live |
| 4 | popup submit starts `disabled` | all popup handlers | wait `:not([disabled])`, verified |
| 5 | `form-helper-error` before submit = CHAT_INTERFACE | `detector.py` | verified |
| 6 | test_form: mandatory test (no skip link) | `handlers/test_form.py` | skips; decision pending (task #3) |

---

## 16. Key Decisions & Rejected Options

| Decision | What was chosen | What was rejected | Why |
|----------|----------------|-------------------|-----|
| Browser automation | Playwright | HH API | API closed Dec 2025 |
| LLM gateway | OpenRouter (BYOK) | Direct Anthropic/OpenAI | Vendor-agnostic, user brings key |
| Orchestration | Python main.py loop | n8n workflow | n8n adds ops complexity, no benefit |
| Scoring timing | BEFORE Apply click | After click | HH tracks clicks; dry-run needs zero side effects |
| React textarea input | `.type(text, delay=10)` | `.fill()` | `.fill()` doesn't fire React `onChange` |
| Search URL type | `/search/vacancy?text=...` | `from=resumelist` | Recommendation feed ignores search params |
| Network error handling | catch all as `skipped_error` | Fatal raise on "Target page closed" | HH legitimately closes pages; fatal raise breaks valid sessions |

---

## 17. Roadmap

| Phase | What | Status |
|-------|------|--------|
| 1 — MIT GitHub | HH.ru adapter + wizard + CLI | **In progress (PHASE1-NEXT)** |
| 2 — Desktop | Tauri/Electron wrapping Python core | Planned |
| 3 — Multisite | Greenhouse/Lever (API-native) → Workday (Playwright) → LinkedIn (Patchright) | Planned |
| 4 — SaaS | Managed LLM, subscriptions, multi-tenant | Future |

**Phase 1 remaining tasks:**
- `suggested_queries` in ResumeData (P0)
- `search_field=everywhere` (P0)
- P0 bugs live verification
- HH pagination (P1)
- test_form behavior decision (P1)

**Phase 3 site priority:** Greenhouse + Lever (API, easy) → Workday (stable selectors) → LinkedIn (Patchright, fragile).
**Skip:** Indeed (DataDome), Zarplata.ru (HH redirect wrapper).
