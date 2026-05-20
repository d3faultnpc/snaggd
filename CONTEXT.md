# CONTEXT.md — Auto-Apply Agent

> For dev agents and contributors. One read = full picture.
> Keep this file up to date when architecture changes.

---

## System Map

```
orchestrator (main.py)
├── HHBrowser (browser.py)          — Playwright: open pages, cookies, click
├── FormDetector (form_detector.py) — classify apply form type from DOM
├── FormHandlers (form_handlers/)   — per-type handlers (hh_modal, chat, questions, ...)
├── LLMCover (llm_cover.py)         — cover letter generation + template fallback
├── HRMatcher (hr_matcher.py)       — HR question answering (keyword-based, no LLM yet)
└── Logger (logger.py)              — applied_log.json + daily logs
```

**Two agents, two contexts:**
- **Playwright agent** — button-clicking, no LLM tokens
- **LLM agent** — cover/score/questions; system prompt = user profile (cached), user msg = vacancy text

---

## Data Flow

```
onboarding.py
  └─► data/resume_facts.md + job_preferences.md + tone_of_voice.md + cover_templates.md

python login.py
  └─► data/hh_cookies.json

main.py
  ├─ loads: config (.env) + data/ profile files + data/hh_cookies.json
  ├─ per vacancy:
  │    browser → vacancy_text
  │    LLMCover.generate(vacancy_text) → cover_letter, match_score, signals
  │    FormDetector.detect(page) → FormType
  │    FormHandler.process(page, cover_letter) → ApplyResult
  │    submission_verified: look for success notification after submit
  │    unknown modal → auto debug snapshot (always, not only in --debug)
  └─ writes: data/applied_log.json, data/pending_actions.json
```

---

## File Registry

| File | Owner | Gitignored |
|------|-------|-----------|
| `data/resume_facts.md` | onboarding | yes |
| `data/job_preferences.md` | onboarding | yes |
| `data/tone_of_voice.md` | onboarding | yes |
| `data/cover_templates.md` | onboarding | yes |
| `data/hh_cookies.json` | login.py | yes |
| `data/applied_log.json` | runtime | yes |
| `data/pending_actions.json` | runtime | yes |
| `data/llm_cache.json` | runtime | yes |
| `HH_Auto/` | symlink → .openclaw (legacy) | yes |
| `config.py` | code | no |
| `hh_unified_n8n_workflow.json` | n8n | no |

---

## Module Contracts

### LLMCover.generate(vacancy_text) → (cover_letter, template_name, signals)
- Input: raw vacancy text string (truncated to 3000 chars internally)
- Output: cover letter string, template name used, list of matched signal tags
- Cache: hash(vacancy_text[:3000]) → result in data/llm_cache.json
- Fallback: template matching if LLM unavailable

### FormDetector.detect(page) → FormInfo
- Input: Playwright Page object (after clicking "Откликнуться")
- Output: FormInfo(form_type, has_salary_field, has_popup_questions, ...)
- Priority order: CHAT_INTERFACE → EMPLOYER_QUESTIONS → HH_MODAL → TEST_FORM → COVER_ONLY → SALARY_FORM → UNKNOWN

### FormHandler.process(page, cover_letter, hr_matcher) → HandlerResult
- Input: page, cover string, HRMatcher instance
- Output: HandlerResult(status, reason, scenario, details)
- Status values: applied | applied_immediate | applied_via_chat | skipped_* | questions_filled

### applied_log.json — entry schema
```json
{
  "url": "string",
  "title": "string",
  "date": "ISO8601",
  "status": "applied | skipped_* | ...",
  "form_type": "hh_modal | immediate | chat | ...",
  "match_score": 0.0,
  "cover_sent": false,
  "submission_verified": false,
  "pending_question": null
}
```

---

## Known Edge Cases

| Case | Where it breaks | Handler | Verified |
|------|----------------|---------|---------|
| popup submit button starts `disabled` | hh_modal.py | wait `:not([disabled])` 5s | yes |
| `textarea.fill()` doesn't trigger React | all handlers | always use `type(text, delay=10)` | yes |
| form-helper-error before submit = already viewed (chatik redirect) | form_detector.py | priority 0б: error+chat_link → CHAT_INTERFACE | yes |
| vacancy-response-question in popup | form_detector.py | priority 0в: → EMPLOYER_QUESTIONS | code only |
| QuestionsHandler submit after fill | questions.py | `_submit()` tries letter_submit + popup_submit + text fallback | code only, NOT tested |
| chatik selectors | chat.py | chatik-chat-message-applicant-action, chatik-new-message-text | NOT verified live |
| Unknown modal blocks content | any handler | → UNKNOWN → auto debug snapshot + pending_actions | planned |
| Cookies expired | browser.py | detect login redirect → stop + message | planned |

---

## Config & Environment

All paths derived from `BASE_DIR = Path(__file__).parent`.
User-specific config via `.env` (see `.env.example`):

```
DATA_DIR=./data            # override data directory
HH_SEARCH_URL=https://...  # built by onboarding from job_preferences
MAX_VACANCIES=3            # per session limit
HEADLESS=false             # run browser headless
LLM_PROVIDER=openrouter    # openrouter | anthropic | ollama
LLM_API_KEY=...
LLM_MODEL=...
PROXY_URL=                 # socks5://127.0.0.1:1080 — for RU users needing VPN for LLM
```

---

## Adapter Pattern (Phase 2+)

Each site = `SiteAdapter` with capability flags:
```python
class SiteAdapter:
    name: str
    enabled: bool
    auth_method: "cookies" | "oauth" | "form"
    resume_source: "api_pull" | "manual"
    apply_method: "api" | "playwright" | "hybrid"
    batch_size: int | None
```

Current: only `adapters/hh/` (Playwright, cookies, no batch).
Phase 2: `adapters/superjob/` (OAuth, API pull resume, hybrid apply).

---

## Prompting Architecture

Three task-specific prompt templates in `prompts/`:
- `cover_letter.md` — generation instructions + anti-hallucination + length limit
- `match_scoring.md` — structured JSON output + strict score thresholds
- `hr_answer.md` — answer HR questions from profile context

User style config in `data/tone_of_voice.md`:
- language, formality, cover_length, salutation, closing
- `sample_cover` field: user's own letter → few-shot style calibration

System prompt (cached): resume_facts + job_preferences + tone_of_voice ≈ 1300 tokens
User message (per vacancy): vacancy_text ≈ 600 tokens

---

## Phase Map

| Module | Phase 1 (now) | Phase 2 |
|--------|--------------|---------|
| `adapters/hh/` | full | — |
| `adapters/superjob/` | stub (disabled) | OAuth + API |
| `core/llm_agent.py` | cover + score | + hr questions on-the-fly |
| `onboarding/wizard.py` | CLI | GUI (Tauri/Electron) |
| batch apply | no | SuperJob if API allows |
| n8n workflow | update after refactor | — |

---

## Dev Workflow

```bash
# Test a change locally
source venv/bin/activate
python main.py --debug --max 3

# Before any git push — scan for sensitive data
python scripts/check_sensitive.py

# Structure check
ls data/   # should show only non-sensitive generated files
```

Dead code: `_docs/archive/` — do not import, kept for reference only.
