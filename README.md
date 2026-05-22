# auto-apply-agent

> Automated job application agent for [HH.ru](https://hh.ru) — the largest job board in Russia and CIS.

Reads your resume, scores each vacancy against it, writes a personalized cover letter, and submits the application — fully automated, one vacancy at a time.

**Why it exists:** HH.ru shut down its public API in December 2025. This agent uses Playwright to drive a real browser session instead.

**Tech stack:** Python 3.10+, Playwright, OpenRouter (BYOK — bring your own key)

---

## What it does

1. Logs into HH.ru using saved cookies (no stored password)
2. Scrapes vacancies from your search URLs
3. Scores each vacancy against your resume (0–100) — skips anything below your threshold
4. Generates a personalized cover letter via LLM, matching the vacancy's language and tone
5. Detects the form type (modal, questionnaire, chatik, etc.) and fills it accordingly
6. Logs every result to `data/applied_log.json`

All LLM calls go through [OpenRouter](https://openrouter.ai). Default model: `google/gemini-2.5-flash-lite` (~$0.0004 per vacancy).

---

## Quickstart

### 1. Install

```bash
git clone https://github.com/d3faultnpc/auto-apply-agent.git
cd auto-apply-agent
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Onboarding wizard (one time)

```bash
python onboarding/wizard.py
```

The wizard creates all required data files in order:

| Block | What it creates |
|-------|----------------|
| D — LLM config | `.env` with your OpenRouter key + model |
| A — Resume | `data/resume_facts.md` from your PDF/DOCX/image |
| B — Job prefs | `data/job_preferences.md` + `data/search_urls.txt` |
| C — Tone | `data/tone_of_voice.md` (cover letter style) |

Get a free OpenRouter key at [openrouter.ai](https://openrouter.ai).

### 3. Log in to HH.ru (one time)

```bash
python login.py
```

Opens a browser window — log in manually. Cookies are saved to `data/hh_cookies.json` and reused on every run.

### 4. Run

```bash
# Dry run first — scores vacancies, never clicks Apply
python main.py --dry-run

# Live run
python main.py

# Limit to N applications
python main.py --max 5
```

| Flag | Description |
|------|-------------|
| `--dry-run` | Score + log vacancies without submitting |
| `--max N` | Stop after N applications |
| `--debug` | Save page screenshots on unknown forms |

---

## Configuration

All config lives in `.env` (created by the wizard):

```bash
LLM_API_KEY=sk-or-...                   # OpenRouter key (required)
LLM_MODEL=google/gemini-2.5-flash-lite  # any OpenRouter model
MIN_SCORE=60                            # skip vacancies scoring below this
MAX_VACANCIES=10                        # max applications per run
HEADLESS=false                          # true = no browser window
DATA_DIR=./data                         # override data directory
PROXY_URL=                              # socks5://... (optional)
```

---

## Architecture

```
main.py (orchestrator)
├── HHAdapter  (adapters/hh/adapter.py)
│   ├── HHBrowser    — Playwright: cookies, navigation, vacancy scraping
│   ├── FormDetector — DOM-based form classification (no LLM)
│   └── FormHandlers
│       ├── hh_modal     — city / metro / schedule dropdowns + cover textarea
│       ├── cover_only   — single cover textarea
│       ├── questions    — employer questionnaire (LLM batch fill)
│       ├── chat         — chatik redirect flow (auto-read employers)
│       ├── test_form    — employer test (skipped by default)
│       └── salary       — salary-only forms (skipped)
├── LLMCover  (llm_cover.py)   — cover letter + scoring, MD5 cache
│   └── LLMAgent (core/llm_agent.py) — OpenRouter gateway
└── Logger    (logger.py)      — data/applied_log.json + daily logs
```

Two contexts, zero overlap:
- **Browser context** — all Playwright actions, zero LLM tokens
- **LLM context** — cover / score / form fill. System prompt (~1300 tokens, cached per session) = resume + preferences + tone. Per-vacancy cost ≈ 600 input tokens.

---

## Project structure

```
adapters/
  base.py          ← SiteAdapter ABC (extend for new job boards)
  hh/              ← HH.ru adapter
core/
  llm_agent.py     ← OpenRouter gateway, prompt cache
onboarding/
  wizard.py        ← CLI setup (blocks D→A→B→C)
  resume_parser.py ← multimodal PDF/DOCX/image → structured resume data
  url_builder.py   ← job preferences → HH search URLs
prompts/           ← LLM prompt templates (cover letter, scoring, form fill)
data/              ← gitignored, created by wizard (your resume, cookies, logs)
scripts/           ← dev utilities (vacancy inspector, label tester)
```

---

## Limitations

- **HH.ru only** — multi-site support is planned for Phase 2
- **Russian job board** — cover letters are generated in the vacancy's language (Russian or English)
- **Cookie-based auth** — if cookies expire, re-run `login.py`
- **Tested on macOS** — should work on Linux; Windows untested

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a new site adapter.

---

## License

MIT — see [LICENSE](LICENSE).
