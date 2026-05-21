# auto-apply-agent

Automated job application agent for [HH.ru](https://hh.ru) (the largest job board in Russia/CIS). Fills out application forms, writes personalized cover letters via LLM, and logs every vacancy it touches.

**Tech stack:** Python 3.8+, Playwright, OpenRouter (BYOK)

> HH.ru closed its public API in December 2025. This agent uses Playwright for all browser interactions.

---

## Quickstart

### 1. Install dependencies

```bash
git clone https://github.com/YOUR_USERNAME/auto-apply-agent.git
cd auto-apply-agent
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Run onboarding wizard (one time)

```bash
python onboarding/wizard.py
```

The wizard walks you through 4 blocks in order:

| Block | What it creates |
|-------|----------------|
| D — LLM config | `.env` with your OpenRouter key + model |
| A — Resume | `data/resume_facts.md` from your PDF/DOCX/image |
| B — Job prefs | `data/job_preferences.md` + `data/search_urls.txt` |
| C — Tone | `data/tone_of_voice.md` (style for cover letters) |

Get your free OpenRouter key at [openrouter.ai](https://openrouter.ai). Recommended model: `google/gemini-2.5-flash-lite`.

### 3. Log in to HH.ru (one time)

```bash
python login.py
```

This opens a browser window — log in manually. Cookies are saved to `data/hh_cookies.json`.

### 4. Run the agent

```bash
python main.py
```

Options:

```
--max N      Process at most N vacancies (default: from .env MAX_VACANCIES)
--debug      Save screenshots on unknown forms + verbose logging
```

---

## Architecture

```
main.py (orchestrator)
├── HHAdapter  (adapters/hh/adapter.py)
│   ├── HHBrowser   — Playwright: cookies, navigation, vacancy scraping
│   ├── FormDetector — DOM-only form classification (no LLM tokens)
│   └── FormHandlers
│       ├── hh_modal     — city / metro / schedule dropdowns + cover textarea
│       ├── cover_only   — single cover textarea
│       ├── questions    — employer questionnaire (LLM batch fill)
│       ├── chat         — chatik redirect flow
│       ├── test_form    — employer test skip
│       └── salary       — skip (salary-only forms)
├── LLMCover  (llm_cover.py)   — cover letter + scoring with MD5 cache
│   └── LLMAgent (core/llm_agent.py) — OpenRouter gateway
├── HRMatcher (hr_matcher.py)  — HR question answering via LLM
└── Logger    (logger.py)      — data/applied_log.json + daily logs
```

**Two contexts, zero overlap:**
- *Playwright context* — all browser actions, zero LLM tokens
- *LLM context* — cover / score / form fill / HR answers. System prompt (≈1300 tokens, cached per session) = resume_facts + job_preferences + tone_of_voice. User message per vacancy ≈ 600 tokens.

### Data flow

```
onboarding/wizard.py  →  data/{resume_facts,job_preferences,tone_of_voice,search_urls}
login.py              →  data/hh_cookies.json

main.py runtime:
  for each search URL:
    scrape vacancy list
    for each vacancy (not in log, not stop-keyword match):
      open page → extract text
      click "Откликнуться"
      [immediate success → log applied_immediate, done]
      LLMCover.generate(vacancy_text)  →  cover letter + match score
      FormDetector.detect(page)        →  form type (DOM only)
      FormHandler.process(...)         →  fill + submit
      Logger.log_result()              →  data/applied_log.json
```

---

## Configuration

All config lives in `.env` (created by the wizard). Available variables:

```bash
LLM_API_KEY=sk-or-...          # OpenRouter key (required)
LLM_MODEL=google/gemini-2.5-flash-lite  # any OpenRouter model
HEADLESS=false                 # true = no browser window
MAX_VACANCIES=10               # max applications per run
DATA_DIR=./data                # override data directory path
PROXY_URL=                     # socks5://... (optional, for RU users)
```

---

## Sandbox testing

Test without touching real vacancies:

```bash
DATA_DIR=sandbox/data python main.py --debug --max 1
```

---

## Adding a new site adapter

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Project structure

```
adapters/
  base.py          ← SiteAdapter ABC
  hh/              ← HH.ru adapter (Playwright + cookies)
core/
  llm_agent.py     ← OpenRouter gateway, system prompt cache
onboarding/
  wizard.py        ← CLI onboarding (blocks D→A→B→C)
  resume_parser.py ← multimodal PDF/DOCX/image → ResumeData
  url_builder.py   ← job preferences → HH search URL
prompts/
  cover_letter.md  ← cover letter generation rules
  match_scoring.md ← JSON scoring schema
  form_fill.md     ← field-filling rules
data/              ← gitignored, created by wizard (user-specific)
scripts/
  check_sensitive.py  ← pre-push sensitive data scanner
```

---

## License

MIT — see [LICENSE](LICENSE).
