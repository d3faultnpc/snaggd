# Changelog

## [0.3.2] — 2026-05-30 · Obstacle Navigation

### Summary
Two sessions of work on top of 0.3.1. Core themes: the agent now senses and
navigates unexpected blocking obstacles (modals, layered forms) via LLM, and
drives toward cover letter delivery as the terminal goal instead of stopping
at the first form. Also adds a REST API for programmatic control and several
yield/reliability fixes.

---

### New: REST API
FastAPI wrapper over the apply agent: `POST /apply/start`, `GET /apply/status`,
`POST /apply/stop`. API key auth via `X-API-Key` header. Swagger UI at `/docs`.
Session termination logging and status codes reference included.

### New: LLM-guided modal dismissal
Unexpected blocking overlays after Apply click are now handled automatically.
The agent detects any `role=alertdialog` or `role=dialog` element, extracts
modal text and visible button labels, and asks the LLM which button to click
(~50 output tokens, no candidate context). Covers current and future HH modals
without hardcoding button text or selectors.

### New: Goal-directed post-handler chat chain (F3)
After any form handler succeeds, the agent re-checks whether a chatik link
appeared on the vacancy page. If present, it routes cover letter delivery
through chatik automatically. Enables multi-layer flows such as:
modal → questionnaire → chatik, handled end-to-end in one vacancy cycle.

### New: HH pagination support
HH moved from infinite scroll to paginated results (50 vacancies/page).
Added `MAX_PAGES` config and `_build_page_url()` for `&page=N` traversal.
Dedup hits no longer count toward the per-session skip budget.

### Fixed: Questionnaire misclassified as mandatory test
Forms with `employer-asking-for-test` header but visible `task-question` fields
were being classified as skippable tests. They now route correctly to
`QuestionsHandler` (collect fields → LLM batch fill → submit).

### Fixed: Chatik submit verification false-negative
`verify_submission()` in ChatHandler: when React rebuilds the input element
after send, a stale `inp is None` check was returning False (applied_unverified).
Fix: treat `inp is None` as confirmed send; added 2 s retry for React clear race.

### Fixed: `dry_run` entries now retryable in live mode
`is_processed()` skips entries with `status=dry_run`, so a subsequent live
run re-scores and applies to vacancies that were only scored in dry-run mode.

### Fixed: LLM output sanitization
`_sanitize_score_result()`: type-guard for `signals`, `matched_skills`, `gaps`,
and `stop_match`. Prevents garbage LLM output (objects, nulls, mixed types)
from corrupting the apply log.

### Fixed: Default LLM model
`LLM_MODEL` default corrected to `deepseek/deepseek-v3.2` (was stale
`anthropic/claude-3-5-haiku` left from an earlier draft).

### Changed: Scroll yield improvement
`_scroll_to_load_all()` added before scraping each search page. HH lazy-loads
~20 of 50 vacancy cards without scroll; the new loop scrolls to stable count
before extracting links, capturing the full page yield.

### New: Developer ergonomics — `--url` flag
`python main.py --url <vacancy-url>` runs a one-shot debug session on a single
vacancy. Implies `--debug` and `--max 1`. Useful for testing specific vacancies
without modifying `search_urls.txt`.

---

## [0.3.1] — 2026-05-29 · Reliability & Onboarding

### Summary
Bug-fix and quality release on top of 0.3.0. Core fixes: auto-read employer
detection (chatik-first flow that skips the apply button entirely), session
cache key collision, employer questionnaire fill, and wizard completeness
(stop_categories, salary hint, employer rating filter). No new user-facing
flows — the existing ones are now more reliable.

---

### Fixed: Auto-read employer detection
Employers with a chatik-first flow embed `vacancy-response-link-view-topic`
on the vacancy page before any button click. The previous code would try to
click the regular apply button, accidentally match a recommendation card link
below the fold, and time out after 30 s. Fix: pre-check for chat link
visibility before attempting any click — if present, skip the button entirely
and go straight to form detection.

### Fixed: Apply button selector priority
`apply_button` selector list reordered: `[data-qa="vacancy-response"]` first,
text-based selectors as fallback. Prevents matching unrelated
`a:has-text("Откликнуться")` links in the recommendation feed.

### Fixed: Session LLM cache key collision
Cache key now compounds `cover_model|llm_model|profile_hash|vacancy_text`.
Profile hash is computed from `candidate.md + job_preferences.md` — updating
your profile invalidates your own entries only. Session scope added: entries
expire after 24 h to prevent stale covers from a prior run.

### Fixed: Employer questionnaire fill (QuestionsHandler)
Employer questions on the apply form are now filled via a single batched LLM
call before the cover letter step. Previously only standard HH modal fields
were filled. Navigation guard added: `Далее` is clicked only when extra
fields are detected. Cover selector updated to the verified
`[data-qa="vacancy-response-letter-submit"]`. Validation error detection
before final submit.

### Fixed: Salary fill quality
`form_fill.md` updated to output a domain-aware number when a salary field is
present, using the candidate's stated range from `candidate.md §Desired Salary`.
Previously returned boilerplate fallback text.

### Changed: Cover letter forbidden openers
Extended: all `Мой опыт` variants (`Мой профессиональный опыт`, `Мой опыт
работы`, etc.) blocked in addition to the previously blocked `Я /
Меня зовут` family.

### Changed: Match context trimmed
`match_context` injected into the cover prompt now carries only `score` and
`role_type`. `matched_skills` and `gaps` removed — they added noise without
improving cover quality in practice.

### New: Dual-model cover
`COVER_MODEL` env var (optional) routes cover generation to a dedicated
model while `LLM_MODEL` handles vacancy scoring. When unset, one model does
both. Documented in `.env.example`.

### New: Offscreen browser
Browser launches offscreen by default (no taskbar clutter). `--debug` flag
restores the visible window at the primary monitor origin.

### New: verify_submission (ChatHandler)
After posting a cover letter via chatik, the agent re-reads the DOM to
confirm acceptance. Logs `applied_via_chat` on confirmed success;
falls back to `applied_unverified` if the confirmation element is absent.

### Changed: Wizard — Block B completeness
New questions added to Block B:
- **Stop industries/domains** — list saved to `job_preferences.md` as
  `stop_categories:` for the LLM semantic filter.
- **Min employer HH rating** — written to `data/filters.json` as
  `min_employer_rating` (hard filter, never sent to LLM).
- **Stop companies** — now also written to `filters.json` `stop_companies`
  in addition to `job_preferences.md`.
- **Desired salary** — free-form (e.g. `от 220 000 руб.`), appended to
  `candidate.md §Desired Salary` for LLM context.

### Changed: Wizard — Block C simplified
Removed `cover_length` and `language` — they contradicted the
`cover_letter.md` prompt's 550–700 char target and built-in language
auto-detection. Block C now writes only `formality` and optional
`sample_cover`.

### Changed: MAX_SKIPS from env var
`max_skips` (vacancy skip budget per session) is now configurable via
`MAX_SKIPS` env var. Default: 10.

---

## [0.3.0] — 2026-05-28 · Context Intelligence

### Summary
Session 16 rework: the two LLM calls (scoring and cover) are now connected.
Scoring output flows into cover generation as context. Candidate's self-reported
career profile shapes both. Prompts are universal — no hardcoded domains or taxonomies.
Model upgraded from Gemini Flash Lite to DeepSeek V3.2.

---

### New: Scoring → Cover context pass-through
Previously the two LLM calls were independent — the cover model knew nothing about
the scoring result. Now `score_vacancy()` output (matched skills, signals, gaps,
vacancy role type) is injected as a compact `SCORING CONTEXT` block before the
vacancy text in the cover prompt. The cover model writes to the real overlap,
not to the vacancy cold.

### New: Candidate Career Profile
Three self-reported fields added to `ResumeData` and `candidate.md`:
- `role_type` — contribution style (builder / operator / strategic / ops / head or any equivalent)
- `edge` — one-sentence unique angle vs other candidates in this role
- `not_looking_for` — contexts to penalise in scoring

Onboarding wizard always asks these questions after resume parse.
Scoring model reads `role_type` to detect role-type mismatch and set penalty.
Cover model reads `edge` via system prompt to write from the candidate's actual angle.

### New: Scoring returns `vacancy_role_type` + `role_type_match`
Scorer classifies the vacancy's required contribution style and compares it to
the candidate's `role_type`. Role mismatch applies –10 to –20 penalty.

### Changed: Universal scoring prompt
Removed all hardcoded domain lists, role taxonomies, and skill examples:
- Domain alignment: profile-relative reasoning — compare vacancy domain to
  CANDIDATE PROFILE, not to a hardcoded list of acceptable domains.
- Role type: scorer uses candidate's own `role_type` vocabulary, not a fixed PM taxonomy.
- Baseline skills: "common to this field" — works for any profession.
- Signals: model generates 3–5 relevant tags per vacancy, not a preset PM tag list.

Works for any candidate (PM, accountant, barista, dentist) without prompt maintenance.

### Changed: Cover letter hook rule
Closing sentence changed from "brief question about the role" to
"observation or hypothesis about a real product/domain challenge."
Prevents closings that sound like the candidate hasn't read the vacancy.

### Changed: Model — Gemini Flash Lite → DeepSeek V3.2
Split test confirmed DeepSeek V3.2 outperforms Gemini Flash Lite on:
- Cover letter length (545–731c vs 327–439c against the same 550–700 target)
- Opening case selection (picks the most relevant project, not the most impressive metric)
- Signals quality (context-specific tags vs generic PM tags)
- Instruction following in general

Resume multimodal parsing (PDF/images) stays on `RESUME_PARSE_MODEL`
(Gemini, separate env var) — not affected by this change.

### Fixed: QuestionsHandler cover letter routing
Cover letter field was incorrectly routed through `fill_form()` instead of using
the pre-generated cover. Fixed: cover fields are detected by label before
`fill_form` is called; only non-cover fields go to the LLM form filler.

### Fixed: Score field sanitization
Some models return score as non-integer (e.g. `"紙 67"`). Added regex extraction
of the first integer from the score field; fallback to 50 if none found.
Prevents `TypeError` on `match_score < min_score` comparison in real runs.

### Changed: Cover letter saved to applied_log
`cover_letter` field added to `score_details` dict in adapter — flows into
`applied_log.json` for every processed vacancy (including skipped ones).

### Changed: Resume parser limits
- Removed 6 000-char input truncation in `_extract_with_llm()`
- `max_tokens` raised from 1 800 → 2 500 in both extraction paths
- `llm_max_input_chars` config raised 3 000 → 5 000 (applies to all LLM calls)

---

## [0.2.0] — 2026-05-27 · Prompting Layer
First structured prompting layer: cover_letter.md, match_scoring.md, form_fill.md.
LLM cache (MD5 keyed). Stop filters: title keywords, company names, semantic categories.
Employer rating signals. Canonical URL dedup.

## [0.1.0] — 2026-05-26 · Foundation
Browser automation with Playwright. Cookie auth. Form detection (chat / questions / modal).
Handler architecture. Applied log. Session loop.
