"""HHAdapter — HH.ru implementation of SiteAdapter."""

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from adapters.base import SiteAdapter
from adapters.hh.browser import HHBrowser
from adapters.hh.detector import FormDetector
from adapters.hh.handlers import FormHandlers
from adapters.hh.handlers.base import FormType, ProcessResult
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

    def __init__(self, data_dir=None, reporter=None):
        from pathlib import Path as _Path
        self._data_dir = _Path(data_dir) if data_dir else CONFIG.data_dir
        self.browser = HHBrowser(reporter=reporter)
        self.detector = FormDetector()
        self.handlers = FormHandlers(data_dir=self._data_dir)
        self.llm_cover = LLMCover(data_dir=self._data_dir)
        # utils.events.EventReporter (API sessions) or None (CLI) — see _say().
        self._reporter = reporter
        self._unverified_count = 0
        self._seen_descriptions: dict = {}  # desc_hash → vacancy_id; resets per session

    def _say(self, message: str, level: str = "info", gui_message: str = None,
             actor: str = "scan", vacancy_id: str = None,
             company: str = None, position: str = None) -> None:
        """print + mirror into the API session's event feed when one is attached.

        The printed string stays byte-identical to the bare print() each call
        site replaced (CLI contract — main.py runs have no reporter and must
        look exactly as before). gui_message, when given, is the humanized
        string a GUI client actually shows (no emoji) — falls back to the
        CLI string (stripped) when omitted, unchanged old behavior.

        vacancy_id here is the run's own per-vacancy sequence number (the
        `index` from run()'s vacancy loop), NOT HH's own vacancy id — it only
        needs to be a stable grouping key shared by every event belonging to
        one vacancy, available from the very first line (before HH's real id
        is even scraped) through the last. actor="llm" marks narration that
        represents the LLM's own output (scoring, cover, form answers) so a
        GUI client can route it to a separate display instead of the scan
        actor's own event log — two independently narrating actors, one
        mechanical, one cognitive.
        """
        print(message)
        if self._reporter is not None:
            self._reporter.emit(
                gui_message if gui_message is not None else message.strip(),
                level=level, actor=actor, vacancy_id=vacancy_id,
                company=company, position=position,
            )

    # ── SiteAdapter interface ─────────────────────────────────────────────────

    def run(self, logger, dry_run: bool = False, debug: bool = False,
            stop_event: Optional[threading.Event] = None,
            pause_event: Optional[threading.Event] = None,
            max_vacancies: Optional[int] = None,
            target_url: Optional[str] = None) -> list:
        """Full session loop. Returns new applied_log entries from this run.

        Three-tier stop filter (all adapter-agnostic config from job_preferences.md):
          Level 0 — title_keywords : exact match in title, before page open, 0 LLM.
          Level 1 — companies      : exact match in company name DOM, after page open, 0 LLM.
          Level 2 — categories     : LLM semantic detection inside score_vacancy call.
        All blocked vacancies are written to applied_log with specific statuses so the
        dashboard can render a complete funnel (found → scored → applied).

        pause_event (sprint N+2, session 54): soft-stop, distinct from
        stop_event. Checked at the same point, top of the per-vacancy loop —
        current vacancy (if any) already finished, next one hasn't opened
        yet. While set, blocks in place rather than returning: does NOT call
        self.close(), the browser (and its own PID/state) stays exactly as
        it was until either resumed (event cleared) or stop_event also
        fires, matching the "finish current, don't advance, keep browser
        alive" spec (session 53).
        """
        applied_log = logger.load_applied_log()
        initial_count = len(applied_log)
        self._unverified_count = 0

        stop_filters = load_stop_filters(CONFIG.data_dir)
        if not stop_filters.is_empty():
            self._say(f"🚫 [{self.name()}] Stop filters active: {stop_filters.summary()}",
                      gui_message=f"Filters active: {stop_filters.summary()}")

        session_dir_base = None
        if debug:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_dir_base = _DEBUG_DIR / f"session_{ts}"
            session_dir_base.mkdir(parents=True, exist_ok=True)
            self._say(f"🐛 [{self.name()}] DEBUG — snapshots in: {session_dir_base}",
                      gui_message="Debug mode — saving detailed snapshots")

        vacancies = self.get_vacancies(target_url=target_url)
        if not vacancies:
            self._say(f"❌ [{self.name()}] No vacancies found", level="error",
                      gui_message="No vacancies matched your search")
            return []
        # Hard safety guard (session 55 live-run incident): target_url must
        # produce exactly one vacancy matching the requested one. Stops the
        # run outright rather than silently falling through to whatever
        # get_vacancies() actually returned — this is deliberately paranoid
        # given the failure mode observed was "override silently didn't
        # apply, normal search vacancies got real applications submitted."
        if target_url:
            if len(vacancies) != 1 or target_url.split('?')[0].rstrip('/') not in vacancies[0][0]:
                self._say(
                    f"❌ [{self.name()}] Single-URL safety check failed — "
                    f"expected exactly [{target_url}], got {vacancies}. Stopping, "
                    f"not risking applying to the wrong vacancy.",
                    level="error",
                    gui_message="Safety check failed — the page that opened doesn't match the requested link, stopping",
                )
                return []
        self._say(f"✅ [{self.name()}] Found {len(vacancies)} vacancies",
                  gui_message=f"Found {len(vacancies)} vacancies to review")

        processed_count = 0
        skip_count = 0
        termination_reason = "completed"
        termination_detail = "All vacancies processed"
        vacancy_limit = max_vacancies if max_vacancies is not None else CONFIG.max_vacancies_per_session

        for url, title, index, search_source in vacancies:
            if stop_event and stop_event.is_set():
                self._say(f"⏹ [{self.name()}] Stop requested via API",
                          gui_message="Stopping — finishing the current vacancy")
                termination_reason = "stopped"
                termination_detail = "Stop requested via API"
                break
            if pause_event and pause_event.is_set():
                self._say(f"⏸ [{self.name()}] Paused before #{index} — browser stays open",
                          gui_message="Paused — waiting for you")
                logger.log_daily(f"[{self.name()}] Paused before #{index}")
                while pause_event.is_set():
                    if stop_event and stop_event.is_set():
                        break
                    time.sleep(1)
                if stop_event and stop_event.is_set():
                    self._say(f"⏹ [{self.name()}] Stop requested via API (while paused)",
                              gui_message="Stopping")
                    termination_reason = "stopped"
                    termination_detail = "Stop requested via API"
                    break
                self._say(f"▶ [{self.name()}] Resumed before #{index}",
                          gui_message="Resumed")
                logger.log_daily(f"[{self.name()}] Resumed before #{index}")
            if processed_count >= vacancy_limit:
                self._say(f"⏹ [{self.name()}] Limit reached: {processed_count}",
                          gui_message="Reached today's run limit")
                termination_reason = "max_vacancies_reached"
                termination_detail = f"{processed_count} vacancies processed"
                break
            if skip_count >= CONFIG.max_skips:
                self._say(f"⏹ [{self.name()}] Skip limit: {skip_count}",
                          gui_message="Too many skips in a row — stopping")
                termination_reason = "max_skips_reached"
                termination_detail = f"{skip_count} consecutive skips"
                break

            existing = logger.is_processed(url, applied_log)
            if existing:
                self._say(f"⏭ #{index} already processed ({existing})",
                          gui_message=f"[SKIP] already applied — \"{title}\"")
                continue  # dedup hit doesn't count toward consecutive skip budget

            # ── Level 0: title keyword filter (no LLM, no page open) ────────────
            title_lower = title.lower()
            matched_kw = next(
                (kw for kw in stop_filters.title_keywords if kw in title_lower), None
            )
            if matched_kw:
                self._say(f"🚫 #{index} title_blocked '{matched_kw}': {title}",
                          gui_message=f"[SKIP] blocked title keyword — \"{title}\"")
                logger.log_result(
                    applied_log, url=url, title=title,
                    status="title_blocked",
                    reason=f"Title keyword: '{matched_kw}'",
                    scenario="skip",
                    search_source=search_source,
                )
                logger.log_daily(f"[{self.name()}] title_blocked #{index}: {title}")
                skip_count += 1
                continue

            print(f"\n{'='*50}")
            self._say(f"[{self.name()}] VACANCY #{index}: {title}",
                      vacancy_id=str(index), position=title)
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
            # Real page title over the passed-in one when available — the
            # passed-in title IS real for a normal search run (scraped from
            # results), but is a 'vacancy/{id}' placeholder in single-URL/
            # target_url mode (no scrape step at all) — session 55, found
            # live sitting in History/Dashboard's Position column.
            effective_title = self.browser.vacancy_title or title
            logger.log_result(
                applied_log, url=canonical, title=effective_title,
                status=result['status'], reason=result['reason'],
                scenario=result.get('scenario', 'unknown'),
                vacancy_id=vacancy_id,
                search_source=search_source,
                **result.get('details', {}),
            )
            # Skip-scenario results (dedup hit after page open, blocked by filters) do not
            # count toward the per-session application budget — only genuine attempts do.
            if result.get('scenario') != 'skip':
                processed_count += 1
                # vacancy_limit, not CONFIG.max_vacancies_per_session — API runs
                # pass max_vacancies= (quota-capped), and the old denominator
                # printed the global config value instead of this run's real one.
                # Plain print, not _say — the topbar's own "N scored" counter
                # already shows this; a duplicate live line would be noise.
                print(f"📈 Progress: {processed_count}/{vacancy_limit}")
            else:
                skip_count += 1
            logger.log_daily(f"Result: {result['status']} — {result['reason']}")
            # Plain print, not _say — this outcome becomes the settled row's
            # own [OK]/[SKIP]/[BLCK] token once logged, not a second live line.
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
        self._say(f"🏁 [{self.name()}] Session ended: {termination_reason} — {termination_detail}",
                  gui_message=f"Run finished — {termination_detail}")
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

    def get_vacancies(self, target_url: str = None) -> list:
        return self.browser.get_vacancy_urls(target_url=target_url)

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
            if not self.browser.open_vacancy(url, index=index):
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
                    self._say(f"   ⏳ Pause {delay/1000:.1f}s (human behavior)",
                              gui_message="Taking a moment before continuing…",
                              vacancy_id=str(index), position=title)
                    self._say(f"   ⏭ Already processed as canonical ({existing}): {canonical}",
                              gui_message="[SKIP] already applied to this vacancy",
                              vacancy_id=str(index), position=title)
                    return {'status': existing, 'reason': f'Already processed: {canonical}', 'scenario': 'skip'}

            delay = random_delay(15000, 25000)
            self._say(f"   ⏳ Pause {delay/1000:.1f}s (reading vacancy)",
                      gui_message="Reading the vacancy…", vacancy_id=str(index), position=title)

            if debug and session_dir:
                self._debug_snapshot(self.browser.get_current_page(), session_dir, "01_vacancy_page")

            # ── Level 1: employer data extraction (no LLM) ──────────────────────
            # Always extract company name and rating — used for both hard filters
            # and LLM context enrichment. Rating = None means employer has no HH reviews.
            company = self.browser.get_company_name()
            employer_rating = self.browser.get_employer_rating()

            if company:
                rating_str = f"{employer_rating}/5.0" if employer_rating is not None else "no reviews"
                self._say(f"   🏢 {company} | HH rating: {rating_str}",
                          gui_message=f"Employer: {company} · rating {rating_str}",
                          vacancy_id=str(index), company=company, position=title)

            # Level 1a — company name exact match
            if stop_filters and stop_filters.companies and company:
                company_lower = company.lower()
                matched_co = next(
                    (co for co in stop_filters.companies if co in company_lower), None
                )
                if matched_co:
                    self._say(f"   🚫 company_blocked '{matched_co}': {company}",
                              gui_message="[SKIP] company on your block list",
                              vacancy_id=str(index), company=company, position=title)
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
                self._say(f"   🚫 rating_blocked {employer_rating} < {stop_filters.min_employer_rating}",
                          gui_message="[SKIP] employer rating too low",
                          vacancy_id=str(index), company=company, position=title)
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

            # ── Duplicate detection (same company + description, different vacancy_id) ──
            # Marks in applied_log with duplicate_of: <first_vacancy_id>. Does NOT skip —
            # cover letter is generated fresh (cover_cache keyed by vacancy_id → natural
            # LLM variation at temperature>0).
            vacancy_id_local = self.browser.vacancy_id
            desc_hash = _desc_hash(company, vacancy_text)
            if desc_hash in self._seen_descriptions:
                duplicate_of = self._seen_descriptions[desc_hash] or None
                self._say(f"   ⚠️ Duplicate description detected (original: {duplicate_of})", level="warn",
                          gui_message="Looks like a repost of a vacancy I've already seen — continuing anyway",
                          vacancy_id=str(index), company=company, position=title)
            else:
                duplicate_of = None
                self._seen_descriptions[desc_hash] = vacancy_id_local or ""

            # ── Enrich vacancy context with employer metadata ────────────────────
            # Prepend company name + HH rating so LLM can factor them into score
            # and signals (e.g. "high_rated_employer", "no_reviews"). This costs
            # ~20 extra tokens and requires no additional LLM call.
            llm_context = _build_employer_header(company, employer_rating) + vacancy_text

            # Score only — cover is generated on demand, later, only by whichever
            # handler actually needs to send one (session 56). Gating stop_match/
            # dry_run/min_score here, before any cover call exists, is the whole
            # point: the old combined generate() paid for a full cover-generation
            # call on every vacancy, even ones about to be filtered by these same
            # checks. Score is cached by text hash; cover (when it happens) is
            # cached by vacancy_id so duplicates (same description, different URL)
            # receive naturally varying cover letters.
            self._say("   🔹 Scoring vacancy...", actor="llm",
                      gui_message="Analyzing this vacancy…",
                      vacancy_id=str(index), company=company, position=title)
            if not llm_cover.score(llm_context):
                # Real reason now surfaced (session 58 live incident — every
                # vacancy in a run failed and the GUI only ever said
                # "postponed", no way to tell a real connection error from an
                # auth/relay problem without console access nobody has from
                # the app).
                _err = llm_cover.last_score_error or "unknown reason"
                self._say(f"   ⚠️ LLM unavailable — skipping vacancy (no score generated): {_err}", level="warn",
                          gui_message=f"[SKIP] model unavailable — {_err}",
                          vacancy_id=str(index), company=company, position=title)
                return {
                    'status': 'skipped_llm_unavailable',
                    'reason': 'LLM unavailable — no score generated',
                    'scenario': 'skip',
                }

            match_score = llm_cover.last_score
            stop_match = llm_cover.last_stop_match
            signals = llm_cover.last_signals
            self._say(f"   📊 Score: {match_score}, signals: {', '.join(signals) if signals else 'none'}"
                      + (f", stop_match: {stop_match}" if stop_match else "")
                      + (f", duplicate_of: {duplicate_of}" if duplicate_of else ""),
                      actor="llm",
                      gui_message=f"Match: {match_score}% · {', '.join(signals) if signals else 'no notable signals'}",
                      vacancy_id=str(index), company=company, position=title)

            score_details = {
                'match_score': match_score,
                'matched_skills': llm_cover.last_matched_skills,
                'gaps': llm_cover.last_gaps,
                'signals': signals,
                'company': company or '',
                'employer_rating': employer_rating,
            }
            if duplicate_of:
                score_details['duplicate_of'] = duplicate_of

            # ── Level 2: semantic stop_match from LLM ───────────────────────────
            if stop_match:
                self._say(f"   🚫 semantic_blocked: LLM detected '{stop_match}'", actor="llm",
                          gui_message=f"[BLCK] not a fit ({stop_match})",
                          vacancy_id=str(index), company=company, position=title)
                return {
                    'status': 'semantic_blocked',
                    'reason': f"LLM detected blocked category: '{stop_match}'",
                    'scenario': 'skip',
                    'details': score_details,
                }

            if dry_run:
                self._say(f"   🔍 Dry-run: score={match_score}, skills={llm_cover.last_matched_skills}",
                          actor="llm",
                          gui_message=f"Dry run — would score {match_score}%, not applying",
                          vacancy_id=str(index), company=company, position=title)
                return {
                    'status': 'dry_run',
                    'reason': f'Dry-run — score: {match_score}',
                    'scenario': 'dry_run',
                    'details': score_details
                }

            # Per-profile override takes precedence — set via ProfileTab's Min Match
            # control (data/profiles/<name>/filters.json), never LLM-derived (this
            # threshold only exists at resume-vs-vacancy comparison time, not at
            # parse time). Falls back to the global CONFIG.min_score default.
            min_score = (stop_filters.min_match
                         if (stop_filters and stop_filters.min_match is not None)
                         else CONFIG.min_score)
            if match_score is not None and match_score < min_score:
                self._say(f"   ⏭ Score {match_score} < min {min_score} — skipping",
                          gui_message=f"[SKIP] match {match_score}% below your threshold",
                          vacancy_id=str(index), company=company, position=title)
                return {
                    'status': 'skipped_score',
                    'reason': f'Score {match_score} below threshold {min_score}',
                    'scenario': 'skip',
                    'details': score_details
                }

            # Auto-read vacancies already have the chat link embedded — clicking any
            # "Откликнуться" would hit a recommendation card and open the wrong popup.
            _pre_chat = self.browser.vacancy_page.query_selector('[data-qa="vacancy-response-link-view-topic"]')
            if _pre_chat and _pre_chat.is_visible():
                self._say("   ✅ Chat link already active (auto-read vacancy) — skipping apply click",
                          gui_message="A conversation is already open with this employer",
                          vacancy_id=str(index), company=company, position=title)
            else:
                self._say("   🔹 Clicking 'Apply'...", gui_message="Clicking \"Apply\"…",
                          vacancy_id=str(index), company=company, position=title)
                if not self.browser.click_apply_button():
                    return {'status': 'skipped_no_apply_button', 'reason': 'Apply button not found'}

            if debug and session_dir:
                self._debug_snapshot(self.browser.get_current_page(), session_dir, "02_after_apply_click")

            current_page = self.browser.get_current_page()
            self._dismiss_blocking_modal(current_page, index=index)

            # Immediate-apply (no form)
            try:
                success_notif = current_page.query_selector(SELECTORS['immediate_success'])
                if success_notif and success_notif.is_visible():
                    # HH often shows chat link alongside the success notification —
                    # that's the only way to send a cover letter after instant apply.
                    chat_el = current_page.query_selector('[data-qa="vacancy-response-link-view-topic"]')
                    if chat_el and chat_el.is_visible():
                        self._say("   ✅ Applied instantly — chat available, routing for cover letter...",
                                  gui_message="Applied instantly — sending a message next",
                                  vacancy_id=str(index), company=company, position=title)
                        # Fall through to detector → ChatHandler
                    else:
                        self._say("   ✅ Application submitted instantly (no form)",
                                  gui_message="[OK] Applied instantly",
                                  vacancy_id=str(index), company=company, position=title)
                        return {
                            'status': 'applied_immediate',
                            'reason': 'Resume submitted without a form',
                            'scenario': 'immediate',
                            'details': score_details
                        }
            except Exception:
                pass

            loop_result, first_form_type = self._process_vacancy_loop(
                current_page, vacancy_text, vacancy_id_local, index, debug, session_dir
            )

            return {
                'status': loop_result.status,
                'reason': loop_result.reason,
                'scenario': loop_result.scenario,
                'details': {
                    'form_type': first_form_type,
                    'goal_reached': loop_result.goal_reached,
                    # Set by whichever handler actually called llm_cover.cover()
                    # (ChatHandler, hh_modal.py's cover step, cover_only.py) —
                    # None if this vacancy never needed a cover at all (e.g.
                    # applied_immediate never reaches the loop).
                    'template_name': llm_cover.last_cover_template_name,
                    **score_details,
                    **(loop_result.details or {})
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

    # ── Goal-directed loop ────────────────────────────────────────────────────

    def _process_vacancy_loop(
        self, page, vacancy_text: str, vacancy_id: str, index: int, debug: bool, session_dir
    ):
        """Detect form type → run handler → repeat until terminal result or MAX_LAYERS.

        Replaces the old flat single-handler dispatch + ad-hoc post-handler chat check.
        Returns (ProcessResult, first_form_type_str).
        """
        MAX_LAYERS = 5
        first_form_type = 'unknown'
        result = None
        prev_form_type = None
        cover_sent_in_modal = False

        for layer in range(MAX_LAYERS):
            self._dismiss_blocking_modal(page, index=index)

            form_info = self.detector.detect(page)
            form_type = form_info.form_type

            if layer == 0:
                first_form_type = form_type.value
                self._say("   🔹 Analysing application form...",
                          gui_message="Figuring out the application form…",
                          vacancy_id=str(index))
            elif form_type != FormType.UNKNOWN:
                # A second (or later) recognized layer on the SAME vacancy means
                # the form genuinely has another step (hh_modal STEP1→STEP2, or a
                # fresh screening step appearing after the previous submit) —
                # worth its own line, not silently folded into the generic
                # loop-bookkeeping message in the else branch below.
                self._say(f"   🔄 Loop layer {layer}: detecting next form layer...",
                          gui_message="This application has another step — continuing…",
                          vacancy_id=str(index))
            else:
                self._say(f"   🔄 Loop layer {layer}: detecting next form layer...")

            # Plain print — folded into the "Figuring out.../another step" line
            # above for the GUI; still useful raw detail for CLI/logs.
            print(f"   📋 Form type: {form_type.value}")
            print(f"   📊 Fields: {form_info.input_count}, Salary: {form_info.has_salary_field}")

            # Salary: hard stop, always skip
            if form_type == FormType.SALARY_FORM:
                if debug and session_dir:
                    self._debug_snapshot(page, session_dir, f"0{3 + layer}_skip_{form_type.value}")
                return ProcessResult(
                    success=False,
                    status='skipped_salary_form',
                    reason='Salary form — always skipped',
                    scenario='skip',
                    is_terminal=True,
                    goal_reached=False
                ), first_form_type

            # UNKNOWN at layer 0: skip immediately
            if form_type == FormType.UNKNOWN and layer == 0:
                if debug and session_dir:
                    self._debug_snapshot(page, session_dir, "03_skip_unknown")
                return ProcessResult(
                    success=False,
                    status='skipped_unknown',
                    reason='Form type not recognized',
                    scenario='skip',
                    is_terminal=True,
                    goal_reached=False
                ), first_form_type

            # UNKNOWN mid-loop: wait 1.5s and retry detector once
            if form_type == FormType.UNKNOWN:
                # Plain print — a retry attempt, not worth its own GUI line.
                print("   ⏳ UNKNOWN mid-loop — waiting 1.5s and retrying detector...")
                page.wait_for_timeout(1500)
                self._dismiss_blocking_modal(page, index=index)
                form_info = self.detector.detect(page)
                form_type = form_info.form_type
                if form_type == FormType.UNKNOWN:
                    self._say("   ⚠️ Still UNKNOWN after retry — stopping loop", level="warn",
                              gui_message="[BLCK] couldn't recognize the application form",
                              vacancy_id=str(index))
                    break

            # Deadlock protection: same form type on consecutive layers means
            # the previous submit didn't navigate away (validation error).
            if prev_form_type is not None and form_type == prev_form_type:
                self._say(f"   ⚠️ {form_type.value} repeated on layer {layer} — submit failed (validation), stopping", level="warn",
                          gui_message="[BLCK] the form didn't accept my last submission",
                          vacancy_id=str(index))
                result = ProcessResult(
                    success=False,
                    status='skipped_form_validation_error',
                    reason=f'Form type {form_type.value} repeated — submit did not navigate away',
                    scenario='questions_validation_error',
                    is_terminal=True,
                    goal_reached=False
                )
                break

            prev_form_type = form_type

            # Run handler
            handler = self.handlers.get_handler(form_type)
            result = handler.process(page, vacancy_text=vacancy_text,
                                     vacancy_id=vacancy_id, llm_cover=self.llm_cover,
                                     cover_sent_via_modal=cover_sent_in_modal,
                                     reporter=self._reporter,
                                     # Distinct from vacancy_id above (HH's own id,
                                     # used for cache keys) — this is the run's
                                     # per-vacancy sequence number, purely a GUI
                                     # grouping key. Handlers pass it through as
                                     # emit(vacancy_id=str(vacancy_seq), ...).
                                     vacancy_seq=index)
            # questions_cover_sent no longer exists (session 56) — questions.py's
            # cover-shaped answers go through the generic fill_form() path, not
            # HH's native cover mechanism, so they were never real grounds for
            # this flag. Only hh_modal.py's own selector-recognized cover step
            # (a real HH data-qa field, not a keyword guess) still sets it.
            if result.status == "hh_modal_cover_sent":
                cover_sent_in_modal = True

            # needs_debug_review: ambiguous mid-form failure (see handlers'
            # _flag_for_debug_review) — an LLM answer that couldn't be applied,
            # a selector that never matched. Capture evidence immediately
            # (unconditional, same as the unverified-count trigger below, not
            # gated behind --debug) since "we don't know what happened" is
            # exactly the case these snapshots are for.
            if result.status == "needs_debug_review":
                reason = (result.details or {}).get("debug_reason", "?")
                # gui_message deliberately [OK] + plain-text qualifier, not a new
                # color — matches Dashboard/History's existing goal_reached
                # counting. vacancy_id kept on this event on purpose: a future
                # "submit this as feedback" feature needs to find it again.
                self._say(f"   🚨 needs_debug_review — saving auto-snapshot ({reason})", level="warn",
                          gui_message="[OK] applied — flagged for your review",
                          vacancy_id=str(index))
                auto_dir = _DEBUG_DIR / f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                self._debug_snapshot(page, auto_dir, "needs_debug_review")

            # DOM щуп: verify submit succeeded
            if result.success:
                verified = handler.verify_submission(page)
                if not verified:
                    self._say("   ⚠️ DOM verification failed — marking as applied_unverified", level="warn",
                              gui_message="Applied, but I couldn't confirm it on the page — flagged for review",
                              vacancy_id=str(index))
                    result.status = "applied_unverified"
                    result.success = False
                    result.goal_reached = False
                    self._unverified_count += 1
                    if self._unverified_count >= 3:
                        # Plain print — internal operational threshold, not user narration.
                        print(f"   🚨 {self._unverified_count} unverified — saving auto-snapshot")
                        auto_dir = _DEBUG_DIR / f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        self._debug_snapshot(page, auto_dir, "unverified")

            if debug and session_dir:
                self._debug_snapshot(page, session_dir, f"0{3 + layer}_layer{layer}_{result.status}")

            if result.is_terminal:
                break

        else:
            # for-else: all MAX_LAYERS exhausted without a terminal break
            self._say(f"   ⚠️ Goal-directed loop exhausted after {MAX_LAYERS} layers", level="warn",
                      gui_message="[BLCK] this application had too many steps — giving up",
                      vacancy_id=str(index))
            if result is not None:
                result.status = 'skipped_loop_exhausted'
                result.goal_reached = False
            else:
                result = ProcessResult(
                    success=False,
                    status='skipped_loop_exhausted',
                    reason=f'Loop exhausted after {MAX_LAYERS} layers',
                    scenario='error',
                    is_terminal=True,
                    goal_reached=False
                )

        if result is None:
            result = ProcessResult(
                success=False,
                status='skipped_unknown',
                reason='No form layer processed',
                scenario='error',
                is_terminal=True,
                goal_reached=False
            )

        return result, first_form_type

    # ── Modal dismissal ───────────────────────────────────────────────────────

    def _dismiss_blocking_modal(self, page, index: int = None) -> bool:
        """LLM-guided dismissal of unexpected blocking modals before form detection.

        Detects role="dialog" overlays that appear after Apply click (e.g. "другая страна"
        confirmation). Extracts text + buttons, asks LLM which to click.
        Returns True if a modal was found and handled, False if none present.

        index: the calling vacancy's run-sequence number, for GUI grouping —
        this is exactly the "hands it to the LLM to interpret" non-canonical
        scenario (unrecognized pop-up), so its narration matters more than
        the mechanical form-detection retries elsewhere in this file.
        """
        # HH uses role="alertdialog" (Magritte) and role="dialog" depending on modal type
        _MODAL_SELECTORS = '[role="alertdialog"], [role="dialog"], [data-qa="magritte-alert"]'
        vid = str(index) if index is not None else None
        try:
            dialog = page.query_selector(_MODAL_SELECTORS)
            if not dialog or not dialog.is_visible():
                return False

            # Modal with a fillable textarea is a form layer — let the loop handle it
            try:
                ta = dialog.query_selector('textarea')
                if ta and ta.is_visible():
                    return False
            except Exception:
                pass

            try:
                modal_text = dialog.inner_text().strip()[:600]
            except Exception:
                return False

            buttons = []
            btn_els = []
            try:
                for btn in page.query_selector_all(
                    '[role="alertdialog"] button, [role="dialog"] button, [data-qa="magritte-alert"] button'
                ):
                    if btn.is_visible():
                        label = btn.inner_text().strip()
                        buttons.append({"index": len(buttons), "label": label or f"btn_{len(buttons)}"})
                        btn_els.append(btn)
            except Exception:
                pass

            if not buttons:
                return False

            self._say(f"   🔲 Blocking modal: \"{modal_text[:80]}\"",
                      gui_message="Ran into an unexpected pop-up — asking the model what to do",
                      vacancy_id=vid)
            # Plain print — the line above already carries this beat for the
            # GUI; llm_agent.py's own call_type="modal_action" tag
            # covers the "reading it" narration once the LLM call
            # below actually fires.
            print(f"   🔘 Buttons: {[b['label'] for b in buttons]}")

            llm = self.llm_cover._agent
            if llm is None:
                return False

            action = llm.ask_modal_action(modal_text, buttons)
            # Plain print — llm_agent.py's own call_type="modal_action" tag
            # (llm actor) already covers this beat's narration.
            print(f"   🤖 Modal action: {action}")

            if action.get("action") == "click":
                idx = action["button_index"]
                if 0 <= idx < len(btn_els):
                    label = buttons[idx]["label"]
                    btn_els[idx].click()
                    self._say(f"   ✅ Modal dismissed: clicked \"{label}\"",
                              gui_message=f"[OK] closed the pop-up ({label})",
                              vacancy_id=vid)
                    page.wait_for_timeout(1500)
                    return True

            self._say("   ⚠️ Modal: skipping dismissal (LLM chose skip or index out of range)", level="warn",
                      gui_message="Left the pop-up as-is — wasn't sure it was safe to close",
                      vacancy_id=vid)
            return False

        except Exception as e:
            self._say(f"   ⚠️ Modal dismissal error: {e}", level="warn",
                      gui_message="Ran into trouble with a pop-up",
                      vacancy_id=vid)
            return False

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
                '[role="alertdialog"]',       # Magritte alert dialogs (e.g. relocation warning)
                '[role="dialog"]',
                '[data-qa="magritte-alert"]',
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


def _desc_hash(company: str, vacancy_text: str) -> str:
    """Short hash of (company, vacancy_text[:2000]) for in-session duplicate detection.

    Uses first 2000 chars of text — enough to distinguish vacancies reliably
    while tolerating minor footer differences in otherwise identical descriptions.
    """
    import hashlib
    key = f"{(company or '').lower()}|{vacancy_text[:2000]}"
    return hashlib.md5(key.encode('utf-8')).hexdigest()[:12]
