# L2 Decisions Archive — pre-2026-05-15

Archived from L2_decisions_log.md during infra sprint (session 22, 2026-05-31).
These entries are foundational decisions that are still valid but rarely referenced.

---

## [2025-12] HH.ru API closed — Playwright-only forever

**Keywords:** HH API, Playwright, API closed, HH Group

**Decision:** HH Group closed their public API in December 2025. They're monetizing their own AI job-search product.

**Impact:** No API pagination, no bulk job fetching, no structured data from API. All interaction via Playwright. Rate limiting via random delays only.

**Scope:** Applies to all HH Group sites: hh.ru, hh.kz, hh.by, hh.uz.

**Lesson:** Don't plan any feature that assumes an HH API endpoint. It doesn't exist. Playwright-first is the only strategy.

---

## [2026-05] n8n workflow removed

**Keywords:** n8n, workflow, orchestration, removed

**Decision:** Removed n8n orchestration. Python `main.py` loop handles all orchestration.

**Why:** n8n added operational complexity (separate process, webhooks, persistent state) without meaningful benefit. Python loop is simpler, debuggable, and testable with standard tools.

**Lesson:** n8n is dead for this project. Reference files archived in `archive/ctx_n8n.md`. Do not reference n8n in new designs.
