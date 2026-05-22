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
from hr_matcher import HRMatcher
from utils.helpers import random_delay

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
        self.hr_matcher = HRMatcher()
        self._unverified_count = 0

    # ── SiteAdapter interface ─────────────────────────────────────────────────

    def run(self, logger, dry_run: bool = False, debug: bool = False) -> list:
        """Full session loop. Returns new applied_log entries from this run."""
        applied_log = logger.load_applied_log()
        initial_count = len(applied_log)
        self._unverified_count = 0

        stop_keywords, stop_companies = self._load_stop_filters()
        if stop_keywords:
            print(f"🚫 [{self.name()}] Stop keywords: {', '.join(stop_keywords)}")
        if stop_companies:
            print(f"🚫 [{self.name()}] Stop companies: {', '.join(stop_companies)}")

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

        for url, title, index in vacancies:
            if processed_count >= CONFIG.max_vacancies_per_session:
                print(f"⏹ [{self.name()}] Limit reached: {processed_count}")
                break
            if skip_count >= CONFIG.max_skips:
                print(f"⏹ [{self.name()}] Skip limit: {skip_count}")
                break

            existing = logger.is_processed(url, applied_log)
            if existing:
                print(f"⏭ #{index} already processed ({existing})")
                skip_count += 1
                continue

            title_lower = title.lower()
            if any(kw in title_lower for kw in stop_keywords):
                matched = next(kw for kw in stop_keywords if kw in title_lower)
                print(f"🚫 #{index} stop keyword '{matched}': {title}")
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
                url, title, index, self.llm_cover, self.hr_matcher,
                debug=debug, session_dir=vac_debug_dir, dry_run=dry_run,
            )

            logger.log_result(
                applied_log, url=url, title=title,
                status=result['status'], reason=result['reason'],
                scenario=result.get('scenario', 'unknown'),
                **result.get('details', {}),
            )
            processed_count += 1
            logger.log_daily(f"Result: {result['status']} — {result['reason']}")
            print(f"📊 Status: {result['status']} — {result['reason']}")
            print(f"📈 Progress: {processed_count}/{CONFIG.max_vacancies_per_session}")

        return applied_log[initial_count:]

    @staticmethod
    def _load_stop_filters() -> tuple[list, list]:
        """Parse stop_keywords and stop_companies from data/job_preferences.md."""
        prefs = CONFIG.data_dir / "job_preferences.md"
        stop_kw, stop_co = [], []
        if not prefs.exists():
            return stop_kw, stop_co
        current = None
        for line in prefs.read_text(encoding="utf-8").splitlines():
            if line.startswith("stop_keywords:"):
                current = "kw"
            elif line.startswith("stop_companies:"):
                current = "co"
            elif line.startswith("  - "):
                val = line[4:].strip().lower()
                if current == "kw":
                    stop_kw.append(val)
                elif current == "co":
                    stop_co.append(val)
            elif line.strip() and not line.startswith(" ") and not line.startswith("#"):
                current = None
        return stop_kw, stop_co

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

    def start(self) -> bool:
        return self.browser.start()

    def close(self) -> None:
        self.browser.close()

    def get_vacancies(self) -> list:
        return self.browser.get_vacancy_urls()

    def process_vacancy(self, url: str, title: str, index: int,
                        llm_cover, hr_matcher,
                        debug: bool = False, session_dir=None, dry_run: bool = False) -> dict:
        """Process one vacancy: open → score → (skip if low score / dry-run) → click Apply → fill → submit."""
        try:
            if not self.browser.open_vacancy(url):
                return {'status': 'skipped_open_error', 'reason': 'Failed to open vacancy'}

            delay = random_delay(15000, 25000)
            print(f"   ⏳ Pause {delay/1000:.1f}s (reading vacancy)")

            if debug and session_dir:
                self._debug_snapshot(self.browser.get_current_page(), session_dir, "01_vacancy_page")

            vacancy_text = self.browser.get_vacancy_text()
            if not vacancy_text:
                return {'status': 'skipped_no_text', 'reason': 'Could not extract vacancy text'}

            # Score and generate cover BEFORE clicking apply — enables dry-run and score gating
            print("   🔹 Scoring vacancy...")
            cover_letter, template_name, signals = llm_cover.generate(vacancy_text)
            match_score = llm_cover.last_score
            print(f"   📊 Score: {match_score}, signals: {', '.join(signals) if signals else 'none'}")

            score_details = {
                'match_score': match_score,
                'matched_skills': llm_cover.last_matched_skills,
                'gaps': llm_cover.last_gaps,
                'signals': signals,
                'template_name': template_name,
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
            result = handler.process(current_page, cover_letter, hr_matcher,
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
            for sel in ['[role="dialog"]', '[data-qa*="modal"]', '[data-qa*="response"]', '.HH-Modal']:
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
