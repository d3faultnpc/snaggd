# Application Status Codes

Canonical reference for all `status` values written to `applied_log.json`.
Used by: API `/log` endpoint, session summary, funnel analytics.

## Severity legend

| Severity | Meaning |
|----------|---------|
| `success` | Vacancy processed successfully (application sent) |
| `soft_skip` | Expected skip — filter or score gate, no error |
| `hard_skip` | Unexpected skip — UI or flow issue worth investigating |
| `error` | Exception or verification failure |
| `info` | Session-level event, not a vacancy entry |

---

## Success

| Status | Severity | Trigger | User action |
|--------|----------|---------|-------------|
| `applied` | success | Form submitted and DOM щуп verified | None |
| `applied_immediate` | success | No form — HH submitted instantly | None |
| `applied_no_cover` | success | Test form skipped, applied without cover | None |
| `applied_via_chat` | success | Submitted via chatik with cover letter | None |
| `applied_via_chat_no_cover` | success | Submitted via chatik, no cover letter slot found | None |
| `applied_unverified` | error | Submitted but DOM щуп failed — cannot confirm | Check debug screenshot; auto-snapshot saved at ≥3 |

---

## Soft skips (expected, no action needed)

| Status | Severity | Trigger | User action |
|--------|----------|---------|-------------|
| `dry_run` | soft_skip | `--dry-run` flag — scored, not submitted | None |
| `skipped_score` | soft_skip | LLM score < `MIN_SCORE` | Lower `MIN_SCORE` or improve resume match |
| `title_blocked` | soft_skip | Title matched a `stop_keywords` entry (Level 0 filter) | Review `job_preferences.md` stop_keywords |
| `semantic_blocked` | soft_skip | LLM detected `stop_category` match (Level 2 filter) | Review `job_preferences.md` stop_categories |
| `skipped_salary_form` | soft_skip | Form requires salary input — always skipped | None (by design) |
| `skipped_test_form` | soft_skip | Test/quiz mandatory, no skip link | None (by design) |
| `chat_redirect` | soft_skip | Employer set auto-read — redirected to chat, no cover slot | Accepted HH behavior |

---

## Hard skips (investigate if frequent)

| Status | Severity | Trigger | File |
|--------|----------|---------|------|
| `skipped_open_error` | hard_skip | Playwright failed to open vacancy page | `adapter.py` |
| `skipped_no_text` | hard_skip | Could not extract vacancy text from DOM | `browser.py` |
| `skipped_no_apply_button` | hard_skip | Apply button not found | `adapter.py` |
| `skipped_unknown` | hard_skip | `FormDetector` returned UNKNOWN type | `detector.py` |
| `skipped_no_inputs` | hard_skip | Form has no fillable inputs | `hh_modal.py` / `cover_only.py` |
| `skipped_no_textarea` | hard_skip | Cover textarea not found | `cover_only.py` |
| `skipped_no_cover_filled` | hard_skip | Cover letter not filled into field | `cover_only.py` |
| `skipped_no_submit` | hard_skip | Submit button not found | `questions.py` / `hh_modal.py` |
| `skipped_form_validation_error` | hard_skip | HH form rejected submission | `questions.py` |
| `skipped_no_chat_button` | hard_skip | Chatik link not found on page | `chat.py` |
| `skipped_chat_fill_error` | hard_skip | Failed to fill chatik input | `chat.py` |
| `skipped_chat_send_error` | hard_skip | Failed to send cover in chatik | `chat.py` |
| `skipped_no_send_button` | hard_skip | Send button not found in chatik | `chat.py` |
| `skipped_edge_case_no_chat` | hard_skip | Modal edge case — chat unavailable after redirect | `hh_modal.py` |
| `skipped_hh_modal` | hard_skip | HH modal handler reached unhandled branch | `hh_modal.py` |
| `skipped_error` | error | Unhandled exception during processing | `adapter.py` — check daily log |

---

## Navigation / edge case

| Status | Severity | Trigger |
|--------|----------|---------|
| `hh_modal_navigation` | error | Modal navigated away unexpectedly during processing |

---

## Session-level entries (`type: "session_end"`)

Not a vacancy status. Written once per session run at termination.

```json
{
  "type": "session_end",
  "reason": "max_vacancies_reached | max_skips_reached | error | completed",
  "detail": "human-readable explanation",
  "timestamp": "ISO8601"
}
```

| `reason` | When |
|----------|------|
| `completed` | All vacancies processed normally |
| `max_vacancies_reached` | Hit `MAX_VACANCIES` limit |
| `max_skips_reached` | Hit `MAX_SKIPS` consecutive skips |
| `error` | Unhandled exception in session loop |

---

## Funnel interpretation

```
found → title_blocked / skipped_score / semantic_blocked  (pre-apply filters)
      → skipped_*                                          (form/UI issues)
      → applied* / applied_unverified                     (success tier)
```

`applied_unverified` warrants investigation: it means the form was reached and
submission attempted, but HH DOM did not confirm success within 5s.
