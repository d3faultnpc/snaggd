# Changelog

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
