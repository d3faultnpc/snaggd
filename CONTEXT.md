# CONTEXT.md — Auto-Apply Agent

> For dev agents and contributors. One read = full picture.
> Updated: 2026-05-21 (session 5, post adapter-refactor)
> Keep this file up to date when architecture changes.

---

## System Map

```
orchestrator (main.py)
├── HHAdapter (adapters/hh/adapter.py)     — HH.ru site adapter (SiteAdapter ABC)
│   ├── HHBrowser (adapters/hh/browser.py) — Playwright: cookies, pages, clicks
│   ├── FormDetector (adapters/hh/detector.py) — DOM-based form classification
│   └── FormHandlers (adapters/hh/handlers/)
│       ├── hh_modal.py     — city/metro/schedule dropdowns + cover textarea
│       ├── cover_only.py   — single cover textarea
│       ├── questions.py    — employer questionnaire (LLM batch fill)
│       ├── chat.py         — chatik redirect flow
│       ├── test_form.py    — employer test/questionnaire skip
│       ├── salary.py       — skip (salary-only forms)
│       └── base.py         — FormType enum, FormInfo, HandlerResult
├── LLMCover (llm_cover.py)                — cover + scoring with cache + static fallback
│   └── LLMAgent (core/llm_agent.py)       — OpenRouter gateway, system prompt cache
├── HRMatcher (hr_matcher.py)              — HR question answering via LLMAgent
└── Logger (logger.py)                     — applied_log.json + daily logs
```

**Two agents, two contexts:**
- **Playwright agent** — all browser actions, zero LLM tokens
- **LLM agent (core/llm_agent.py)** — cover / score / form fill / HR answers
  System prompt (cached per session): resume_facts + job_preferences + tone_of_voice ≈ 1300 tok
  User message (per vacancy): vacancy_text ≈ 600 tok

---

## Data Flow

```
onboarding/wizard.py  (run once, order: D → A → B → C)
  Block D → .env          (LLM_API_KEY, LLM_MODEL, HEADLESS, MAX_VACANCIES)
  Block A → data/resume_facts.md
  Block B → data/search_urls.txt  +  data/job_preferences.md
  Block C → data/tone_of_voice.md

login.py  →  data/hh_cookies.json

main.py runtime:
  LLMAgent: loads resume_facts + job_preferences + tone_of_voice → system prompt (cached)
  HHAdapter.verify()      → cookies exist + search_urls configured
  HHAdapter.start()       → launches Playwright browser with cookies
  HHAdapter.get_vacancies() → scrapes all search_urls, deduplicates by URL

  per vacancy:
    [SKIP if: already in applied_log / stop_keyword in title]
    Playwright: open vacancy page → wait (human-like) → extract vacancy text
    Playwright: click "Откликнуться"
    [CHECK: immediate success notification → applied_immediate, done]
    [SKIP if: SALARY_FORM / UNKNOWN]
    LLMCover.generate(vacancy_text):
      → LLMAgent.generate_cover()   → personalized cover letter
      → LLMAgent.score_vacancy()    → {score, matched_skills, gaps, signals}
    FormDetector.detect(page)       → form_type (DOM only, no LLM)
    FormHandler.process(page, cover, hr_matcher):
      hh_modal:    Playwright fills dropdowns + cover textarea → submit
      cover_only:  Playwright types cover → submit
      questions:   LLMAgent.fill_form(fields) → Playwright types each answer
      chat:        Playwright clicks chat link → types cover in chatik
      test_form:   Playwright clicks "without questions" link
    Logger.log_result() → data/applied_log.json
```

---

## File Registry

| File | Owner | Gitignored |
|------|-------|-----------|
| `data/resume_facts.md` | onboarding Block A | yes |
| `data/job_preferences.md` | onboarding Block B | yes |
| `data/search_urls.txt` | onboarding Block B | yes |
| `data/tone_of_voice.md` | onboarding Block C | yes |
| `data/hh_cookies.json` | login.py | yes |
| `data/applied_log.json` | runtime | yes |
| `data/pending_actions.json` | runtime | yes |
| `data/llm_cache.json` | runtime | yes |
| `config.py` | code | no |
| `prompts/cover_letter.md` | code | no |
| `prompts/match_scoring.md` | code | no |
| `prompts/form_fill.md` | code | no |

> `data/` is created by `python onboarding/wizard.py` — one folder per user, gitignored, never in repo.

---

## Module Contracts

### LLMCover.generate(vacancy_text) → (cover_letter, template_name, signals)
- Wraps LLMAgent with MD5 cache (data/llm_cache.json)
- Fallback: static minimal text if LLM unavailable ("Добрый день. Заинтересован...")
- Sets `self.last_score`, `self.last_matched_skills`, `self.last_gaps` after each call

### FormDetector.detect(page) → FormInfo
- DOM only, no LLM, must run < 1s
- Priority order: CHAT_INTERFACE → EMPLOYER_QUESTIONS → HH_MODAL → TEST_FORM → COVER_ONLY → SALARY_FORM → UNKNOWN
- Key signal: `form-helper-error + chat_link` → CHAT_INTERFACE (viewed vacancy edge case)

### FormHandler.process(page, cover, hr_matcher) → HandlerResult
- Status values: `applied` | `applied_immediate` | `applied_via_chat` | `skipped_*` | `questions_filled`

### applied_log.json entry schema
```json
{
  "url": "string",
  "title": "string",
  "date": "ISO8601",
  "status": "applied | skipped_* | ...",
  "form_type": "hh_modal | immediate | chat | questions | ...",
  "match_score": 75,
  "matched_skills": ["skill1"],
  "gaps": ["missing_skill"],
  "signals": ["platform", "b2b"],
  "cover_sent": true,
  "submission_verified": false,
  "pending_question": null
}
```

---

## Known Edge Cases

| Case | Where | Handler | Verified live |
|------|--------|---------|---------------|
| popup submit starts `disabled` | hh_modal.py | wait `:not([disabled])` 5s | yes |
| `textarea.fill()` skips React events | all handlers | always `type(text, delay=10)` | yes |
| form-helper-error before submit = vacancy viewed (chatik) | detector.py | priority 0б: error+chat_link → CHAT_INTERFACE | yes |
| vacancy-response-question in popup | detector.py | priority 0в: → EMPLOYER_QUESTIONS | code only |
| QuestionsHandler submit after fill | questions.py | `_submit()` tries letter_submit + popup_submit | **NOT tested live** |
| chatik selectors | chat.py | chatik-chat-message-applicant-action, chatik-new-message-text | **NOT verified live** |

---

## Config & Environment

```bash
# .env (gitignored, created by wizard Block D)
LLM_API_KEY=sk-or-...          # OpenRouter key
LLM_MODEL=google/gemini-2.5-flash-lite
LLM_PROVIDER=openrouter
HEADLESS=false
MAX_VACANCIES=10
DATA_DIR=./data                 # override if needed
PROXY_URL=                      # socks5://... for RU users
```

---

## Adapter Pattern

```python
class SiteAdapter(ABC):
    def name(self) -> str          # "hh.ru"
    def auth_method(self) -> str   # "cookie"
    def verify(self) -> bool       # pre-flight check
    def start(self) -> bool        # launch browser
    def close(self) -> None
    def get_vacancies(self) -> list
    def process_vacancy(...) -> dict
```

Current: `adapters/hh/` (Playwright + cookies).
Next: `adapters/superjob/` stub, then Greenhouse/Lever (API), Workday (Playwright).

---

## Dev Workflow

```bash
# Sandbox test (safe, isolated)
DATA_DIR=sandbox/data python main.py --debug --max 1

# Before any git push
python scripts/check_sensitive.py   # must return 0 hits

# Single onboarding block
python onboarding/wizard.py --block d
```

---

## OSS Roadmap (brief)

| Phase | What | Status |
|-------|------|--------|
| 1 — MIT GitHub | HH.ru adapter + wizard + README/LICENSE | **in progress** |
| 2 — Desktop | Tauri/Electron app over Python core | planned |
| 3 — Multisite | Greenhouse/Lever (API), Workday/LinkedIn (Playwright) | planned |
| 4 — SaaS | Managed LLM, subscription, multi-tenant | future |
