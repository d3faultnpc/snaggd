"""HHAdapter — HH.ru implementation of SiteAdapter."""

import os
from datetime import datetime
from pathlib import Path

from adapters.base import SiteAdapter
from adapters.hh.browser import HHBrowser
from adapters.hh.detector import FormDetector
from adapters.hh.handlers import FormHandlers
from adapters.hh.handlers.base import FormType
from config import CONFIG, SELECTORS
from llm_cover import LLMCover
from utils.helpers import random_delay
from utils.filters import StopFilters, load_stop_filters

_DEBUG_DIR = Path(os.getenv("DEBUG_DIR", Path(__file__).parent.parent.parent / "debug_screenshots"))


class HHAdapter(SiteAdapter):
    """HH.ru adapter: Playwright-only (HH API closed Dec 2025)."""

    def name(self) -> str:
        return "hh.ru"

    def auth_method(self) -> str:
        return "cookie"

    def __init__(self):
        self.browser = HHBrowser()
        self.detector = FormDetector()
        self.handlers = FormHandlers()
        self.llm_cover = LLMCover()
        self._unverified_count = 0

    # ── SiteAdapter interface ─────────────────────────────────────────────────

    def run(self, logger, dry_run: bool = False, debug: bool = False) -> list:
        """Full session loop. Returns new applied_log entries from this run.

        Three-tier stop filter (all adapter-agnostic config from job_preferences.md):
          Level 0 — title_keywords : exact match in title, before page open, 0 LLM.
          Level 1 — companies      : exact match in company name DOM, after page open, 0 LLM.
          Level 2 — categories     : LLM semantic detection inside score_vacancy call.
        All blocked vacancies are written to applied_log with specific statuses so the
        dashboard can render a complete funnel (found → scored → applied).
        """
        applied_log = logger.load_applied_log()
        initial_count = len(applied_log)
        self._unverified_count = 0

        stop_filters = load_stop_filters(CONFIG.data_dir)
        if not stop_filters.is_empty():
            print(f"🚫 [{self.name()}] Stop filters active: {stop_filters.summary()}")

        session_dir_base = None
        if debug:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_dir_base = _DEBUG_DIR / f"session_{ts}"
            session_dir_base.mkdir(parents=True, exist_ok=True)
            print(f"🐛 [{self.name()}] DEBUG — snapshots in: {session_dir_base}")

        vacancies = self.get_vacancies()
        if not vacancies:
            print(f"❌ [{self.name()}] No vacancies found")
            return []
        print(f"✅ [{self.name()}] Found {len(vacancies)} vacancies")

        processed_count = 0
        skip_count = 0
        termination_reason = "completed"
        termination_detail = "All vacancies processed"

        for url, title, index in vacancies:
            if processed_count >= CONFIG.max_vacancies_per_session:
                print(f"⏹ [{self.name()}] Limit reached: {processed_count}")
                termination_reason = "max_vacancies_reached"
                termination_detail = f"{processed_count} vacancies processed"
                break
            if skip_count >= CONFIG.max_skips:
                print(f"⏹ [{self.name()}] Skip limit: {skip_count}")
                termination_reason = "max_skips_reached"
                termination_detail = f"{skip_count} consecutive skips"
                break

            existing = logger.is_processed(url, applied_log)
            if existing:
                print(f"⏭ #{index} already processed ({existing})")
                skip_count += 1
                continue

            # ── Level 0: title keyword filter (no LLM, no page open) ────────────
            title_lower = title.lower()
            matched_kw = next(
                (kw for kw in stop_filters.title_keywords if kw in title_lower), None
            )
            if matched_kw:
                print(f"🚫 #{index} title_blocked '{matched_kw}': {title}")
                logger.log_result(
                    applied_log, url=url, title=title,
                    status="title_blocked",
                    reason=f"Title keyword: '{matched_kw}'",
                    scenario="skip",
                )
                logger.log_daily(f"[{self.name()}] title_blocked #{index}: {title}")
                skip_count += 1
                continue

            print(f"\n{'='*50}")
            print(f"[{self.name()}] VACANCY #{index}: {title}")
            print(f"URL: {url}")
            logger.log_daily(f"[{self.name()}] VACANCY #{index}: {title} — {url}")

            vac_debug_dir = None
            if debug and session_dir_base:
                safe = "".join(c for c in title[:30] if c.isalnum() or c in " _-").strip()
                vac_debug_dir = session_dir_base / f"{index:02d}_{safe}"

            result = self.process_vacancy(
                url, title, index, self.llm_cover,
                debug=debug, session_dir=vac_debug_dir, dry_run=dry_run,
                stop_filters=stop_filters, logger=logger, applied_log=applied_log,
            )

            # Use canonical URL (hh.ru/vacancy/ID) for log storage so future runs
            # can dedup by vacancy ID regardless of tracking URL meta= changes.
            canonical = self.browser.canonical_url or url
            vacancy_id = self.browser.vacancy_id
            logger.log_result(
                applied_log, url=canonical, title=title,
                status=result['status'], reason=result['reason'],
                scenario=result.get('scenario', 'unknown'),
                vacancy_id=vacancy_id,
                **result.get('details', {}),
            )
            # Skip-scenario results (dedup hit after page open, blocked by filters) do not
            # count toward the per-session application budget — only genuine attempts do.
            if result.get('scenario') != 'skip':
                processed_count += 1
                print(f"📈 Progress: {processed_count}/{CONFIG.max_vacancies_per_session}")
            else:
                skip_count += 1
            logger.log_daily(f"Result: {result['status']} — {result['reason']}")
            print(f"📊 Status: {result['status']} — {result['reason']}")

        new_entries = applied_log[initial_count:]
        logger.log_result(
            applied_log,
            type="session_end",
            reason=termination_reason,
            detail=termination_detail,
            processed=processed_count,
        )
        logger.log_daily(f"[{self.name()}] Session ended: {termination_reason} — {termination_detail}")
        print(f"🏁 [{self.name()}] Session ended: {termination_reason} — {termination_detail}")
        return new_entries

    def verify(self) -> bool:
        """Check cookies exist and at least one search URL is configured."""
        import os
        cookies_ok = Path(CONFIG.cookies_path).exists()
        urls_ok = (CONFIG.search_urls_path.exists() and
                   bool(CONFIG.search_urls_path.read_text(encoding="utf-8").strip()))
        # Backward-compat: old HH_SEARCH_URL env var counts as configured
        if not urls_ok:
            urls_ok = bool(os.getenv("HH_SEARCH_URL", ""))
        if not cookies_ok:
            print(f"   ❌ Cookies not found: {CONFIG.cookies_path}")
        if not urls_ok:
            print(f"   ❌ No search URLs configured — run: python onboarding/wizard.py --block b")
        return cookies_ok and urls_ok

    def start(self, debug: bool = False) -> bool:
        return self.browser.start(debug=debug)

    def close(self) -> None:
        self.browser.close()

    def get_vacancies(self) -> list:
        return self.browser.get_vacancy_urls()

    def process_vacancy(self, url: str, title: str, index: int,
                        llm_cover,
                        debug: bool = False, session_dir=None, dry_run: bool = False,
                        stop_filters=None, logger=None, applied_log=None) -> dict:
        """Process one vacancy: open → filter → score → apply → fill → submit.

        stop_filters levels handled here:
          Level 1 — company name check (after page open, before LLM)
          Level 2 — semantic stop_match check (inside LLM score call)
        Level 0 (title) is handled upstream in run() before page open.
        """
        try:
            if not self.browser.open_vacancy(url):
                return {'status': 'skipped_open_error', 'reason': 'Failed to open vacancy'}

            # Canonical dedup check: tracking URL resolves to hh.ru/vacancy/ID after redirect.
            # Catches vacancies already logged under canonical URL even when scraped as adsrv tracking URL.
            canonical = self.browser.canonical_url
            if canonical and logger is not None and applied_log is not None:
                existing = logger.is_processed(canonical, applied_log)
                if existing:
                    # Human-like pause even for skipped vacancies — avoids open→close instantly pattern
                    # that triggers HH bot filters.
                    delay = random_delay(7000, 10000)
                    print(f"   ⏳ Pause {delay/1000:.1f}s (human behavior)")
                    print(f"   ⏭ Already processed as canonical ({existing}): {canonical}")
                    return {'status': existing, 'reason': f'Already processed: {canonical}', 'scenario': 'skip'}

            delay = random_delay(15000, 25000)
            print(f"   ⏳ Pause {delay/1000:.1f}s (reading vacancy)")

            if debug and session_dir:
                self._debug_snapshot(self.browser.get_current_page(), session_dir, "01_vacancy_page")

            # ── Level 1: employer data extraction (no LLM) ──────────────────────
            # Always extract company name and rating — used for both hard filters
            # and LLM context enrichment. Rating = None means employer has no HH reviews.
            company = self.browser.get_company_name()
            employer_rating = self.browser.get_employer_rating()

            if company:
                rating_str = f"{employer_rating}/5.0" if employer_rating is not None else "no reviews"
                print(f"   🏢 {company} | HH rating: {rating_str}")

            # Level 1a — company name exact match
            if stop_filters and stop_filters.companies and company:
                company_lower = company.lower()
                matched_co = next(
                    (co for co in stop_filters.companies if co in company_lower), None
                )
                if matched_co:
                    print(f"   🚫 company_blocked '{matched_co}': {company}")
                    return {
                        'status': 'company_blocked',
                        'reason': f"Company '{company}' matches stop list: '{matched_co}'",
                        'scenario': 'skip',
                        'details': {'company': company, 'employer_rating': employer_rating},
                    }

            # Level 1b — employer rating threshold
            # Only skip when rating is explicitly present AND below threshold.
            # None (no reviews) → unknown → do NOT skip, let LLM decide.
            if (stop_filters and stop_filters.min_employer_rating is not None
                    and employer_rating is not None
                    and employer_rating < stop_filters.min_employer_rating):
                print(f"   🚫 rating_blocked {employer_rating} < {stop_filters.min_employer_rating}")
                return {
                    'status': 'rating_blocked',
                    'reason': (
                        f"Employer rating {employer_rating} below threshold "
                        f"{stop_filters.min_employer_rating} — {company or 'unknown'}"
                    ),
                    'scenario': 'skip',
                    'details': {'company': company or '', 'employer_rating': employer_rating},
                }

            vacancy_text = self.browser.get_vacancy_text()
            if not vacancy_text:
                return {'status': 'skipped_no_text', 'reason': 'Could not extract vacancy text'}

            # ── Enrich vacancy context with employer metadata ────────────────────
            # Prepend company name + HH rating so LLM can factor them into score
            # and signals (e.g. "high_rated_employer", "no_reviews"). This costs
            # ~20 extra tokens and requires no additional LLM call.
            llm_context = _build_employer_header(company, employer_rating) + vacancy_text

            # Score first, then cover — score may reveal stop_match before cover is used.
            # llm_context = employer header (~20t) + vacancy_text. Cached by MD5.
            print("   🔹 Scoring vacancy...")
            cover_letter, template_name, signals = llm_cover.generate(llm_context)
            match_score = llm_cover.last_score
            stop_match = llm_cover.last_stop_match
            print(f"   📊 Score: {match_score}, signals: {', '.join(signals) if signals else 'none'}"
                  + (f", stop_match: {stop_match}" if stop_match else ""))

            score_details = {
                'match_score': match_score,
                'matched_skills': llm_cover.last_matched_skills,
                'gaps': llm_cover.last_gaps,
                'signals': signals,
                'template_name': template_name,
                'company': company or '',
                'employer_rating': employer_rating,
                'cover_letter': cover_letter,
            }

            # ── Level 2: semantic stop_match from LLM ───────────────────────────
            if stop_match:
                print(f"   🚫 semantic_blocked: LLM detected '{stop_match}'")
                return {
                    'status': 'semantic_blocked',
                    'reason': f"LLM detected blocked category: '{stop_match}'",
                    'scenario': 'skip',
                    'details': score_details,
                }

            if dry_run:
                print(f"   🔍 Dry-run: score={match_score}, skills={llm_cover.last_matched_skills}")
                return {
                    'status': 'dry_run',
                    'reason': f'Dry-run — score: {match_score}',
                    'scenario': 'dry_run',
                    'details': score_details
                }

            if match_score is not None and match_score < CONFIG.min_score:
                print(f"   ⏭ Score {match_score} < min {CONFIG.min_score} — skipping")
                return {
                    'status': 'skipped_score',
                    'reason': f'Score {match_score} below threshold {CONFIG.min_score}',
                    'scenario': 'skip',
                    'details': score_details
                }

            # Auto-read vacancies already have the chat link embedded — clicking any
            # "Откликнуться" would hit a recommendation card and open the wrong popup.
            _pre_chat = self.browser.vacancy_page.query_selector('[data-qa="vacancy-response-link-view-topic"]')
            if _pre_chat and _pre_chat.is_visible():
                print("   ✅ Chat link already active (auto-read vacancy) — skipping apply click")
            else:
                print("   🔹 Clicking 'Apply'...")
                if not self.browser.click_apply_button():
                    return {'status': 'skipped_no_apply_button', 'reason': 'Apply button not found'}

            if debug and session_dir:
                self._debug_snapshot(self.browser.get_current_page(), session_dir, "02_after_apply_click")

            current_page = self.browser.get_current_page()

            # Immediate-apply (no form)
            try:
                success_notif = current_page.query_selector(SELECTORS['immediate_success'])
                if success_notif and success_notif.is_visible():
                    # HH often shows chat link alongside the success notification —
                    # that's the only way to send a cover letter after instant apply.
                    chat_el = current_page.query_selector('[data-qa="vacancy-response-link-view-topic"]')
                    if chat_el and chat_el.is_visible():
                        print("   ✅ Applied instantly — chat available, routing for cover letter...")
                        # Fall through to detector → ChatHandler
                    else:
                        print("   ✅ Application submitted instantly (no form)")
                        return {
                            'status': 'applied_immediate',
                            'reason': 'Resume submitted without a form',
                            'scenario': 'immediate',
                            'details': score_details
                        }
            except Exception:
                pass

            print("   🔹 Analysing application form...")
            form_info = self.detector.detect(current_page)
            print(f"   📋 Form type: {form_info.form_type.value}")
            print(f"   📊 Fields: {form_info.input_count}, Salary: {form_info.has_salary_field}")

            if form_info.form_type in (FormType.SALARY_FORM, FormType.UNKNOWN):
                if debug and session_dir:
                    self._debug_snapshot(current_page, session_dir, f"03_skip_{form_info.form_type.value}")
                return {
                    'status': f'skipped_{form_info.form_type.value}',
                    'reason': f'Form skipped: {form_info.form_type.value}',
                    'scenario': 'skip',
                    'details': {'form_type': form_info.form_type.value, **score_details}
                }

            handler = self.handlers.get_handler(form_info.form_type)
            result = handler.process(current_page, cover_letter,
                                     vacancy_text=vacancy_text)

            # F2: DOM щуп — verify submit actually succeeded
            if result.success:
                verified = handler.verify_submission(current_page)
                if not verified:
                    print("   ⚠️ DOM verification failed — marking as applied_unverified")
                    result.status = "applied_unverified"
                    result.success = False
                    self._unverified_count += 1
                    if self._unverified_count >= 3:
                        print(f"   🚨 {self._unverified_count} unverified submissions — saving auto-snapshot")
                        auto_dir = _DEBUG_DIR / f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        self._debug_snapshot(current_page, auto_dir, "unverified")

            if debug and session_dir:
                self._debug_snapshot(current_page, session_dir, f"03_after_handler_{result.status}")

            return {
                'status': result.status,
                'reason': result.reason,
                'scenario': result.scenario,
                'details': {
                    'form_type': form_info.form_type.value,
                    **score_details,
                    **(result.details or {})
                }
            }

        except Exception as e:
            err = str(e)
            if debug and session_dir:
                try:
                    self._debug_snapshot(self.browser.get_current_page(), session_dir, "error")
                except Exception:
                    pass
            return {
                'status': 'skipped_error',
                'reason': f'Processing error: {err}',
                'scenario': 'error'
            }

        finally:
            self.browser.close_vacancy()

    # ── Debug helper ──────────────────────────────────────────────────────────

    @staticmethod
    def _debug_snapshot(page, session_dir: Path, label: str) -> None:
        """Save screenshot + HTML + data-qa list for a debug session."""
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(session_dir / f"{label}.png"), full_page=False)

            modal = None
            for sel in [
                '[data-qa="chatik-root"]',   # chatik modal (must come before generic response selector)
                '[role="dialog"]',
                '[data-qa*="modal"]',
                '.HH-Modal',
            ]:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    modal = el
                    break
            html_content = modal.inner_html() if modal else page.inner_html('body')
            (session_dir / f"{label}.html").write_text(html_content, encoding="utf-8")

            data_qa = page.evaluate("""() => {
                const els = document.querySelectorAll('[data-qa]');
                const vals = new Set();
                els.forEach(el => vals.add(el.getAttribute('data-qa')));
                return Array.from(vals).sort();
            }""")
            (session_dir / f"{label}_data_qa.txt").write_text("\n".join(data_qa), encoding="utf-8")

            print(f"   📸 [{label}] screenshot + HTML + {len(data_qa)} data-qa → {session_dir.name}/")
        except Exception as e:
            print(f"   ⚠️ debug_snapshot [{label}] error: {e}")


# ── Module-level helpers ──────────────────────────────────────────────────────

def _build_employer_header(company, rating) -> str:
    """Build a short employer metadata block to prepend to vacancy_text for LLM context.

    ~20 extra tokens. Lets the LLM factor employer reputation into score and signals:
      - high rating (≥4.5) → signal "top_employer"
      - low rating (< 3.5) → signal "low_rated_employer" (if not already filtered out)
      - no reviews       → signal "no_hh_reviews" (unknown reputation)

    Returns empty string if no employer data is available.
    """
    if not company and rating is None:
        return ""
    parts = []
    if company:
        parts.append(f"Employer: {company}")
    if rating is not None:
        parts.append(f"HH Employer Rating: {rating}/5.0")
    else:
        parts.append("HH Employer Rating: no reviews on HH")
    return "\n".join(parts) + "\n\n"
