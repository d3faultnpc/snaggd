import atexit
import json
import os
import re
import time
from itertools import zip_longest
from typing import List, Optional
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from config import CONFIG, SELECTORS

from .dom import find_visible, iter_visible

class HHBrowser:
    # data_dir: the PROFILE's own directory. Previously absent, which meant
    # _load_search_urls() below could only ever read the module-level
    # CONFIG.search_urls_path — and CONFIG is instantiated once, at import, from
    # whatever DATA_DIR happened to be set then. That is correct for the CLI
    # (one profile per process, DATA_DIR exported before config is imported) and
    # silently wrong for any long-lived host that serves several profiles from
    # one process: every profile read the same global search_urls.txt.
    # HHAdapter already received and threaded a per-profile data_dir into
    # FormHandlers and LLMCover; search URLs were simply never included.
    def __init__(self, reporter=None, data_dir=None):
        from pathlib import Path as _Path
        self._data_dir = _Path(data_dir) if data_dir else CONFIG.data_dir
        self.playwright = None
        self._pw_manager = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.vacancy_page: Optional[Page] = None
        self._canonical_url: Optional[str] = None
        self._vacancy_id: Optional[str] = None
        self._vacancy_title: Optional[str] = None
        # Run's own per-vacancy sequence number (adapter.py's `index`), NOT
        # HH's own vacancy id above — set once per open_vacancy() call, read
        # by every method that narrates about the vacancy currently open
        # (get_vacancy_text, click_apply_button) so their GUI events group
        # under the right Terminal sub-header without threading `index`
        # through every one of those signatures individually.
        self._vacancy_seq: Optional[int] = None
        # utils.events.EventReporter (API sessions) or None (CLI) — mirrors
        # HHAdapter._say() exactly (session 55: this class had zero reporter
        # wiring, so the whole search/scrape phase never reached the GUI
        # Terminal, only adapter.py's own per-vacancy narration did).
        self._reporter = reporter
        atexit.register(self.close)

    def _say(self, message: str, level: str = "info", gui_message: str = None,
             actor: str = "scan", vacancy_id: str = None,
             company: str = None, position: str = None) -> None:
        """print + mirror into the API session's event feed when one is attached.
        Same contract as HHAdapter._say() — printed string stays byte-identical
        to the print() each call site replaced, event copy drops leading
        CLI indentation (no meaning in the GUI stream). gui_message/actor/
        vacancy_id/company/position: see HHAdapter._say()'s own docstring —
        identical semantics, kept in sync deliberately."""
        print(message)
        if self._reporter is not None:
            self._reporter.emit(
                gui_message if gui_message is not None else message.strip(),
                level=level, actor=actor, vacancy_id=vacancy_id,
                company=company, position=position,
            )

    def _vacancy_gui_id(self) -> Optional[str]:
        """Current vacancy's GUI-grouping id (see _vacancy_seq above), or None
        before any vacancy has been opened / after close_vacancy()."""
        return str(self._vacancy_seq) if self._vacancy_seq is not None else None

    @property
    def canonical_url(self) -> Optional[str]:
        return self._canonical_url

    @property
    def vacancy_id(self) -> Optional[str]:
        return self._vacancy_id

    @property
    def vacancy_title(self) -> Optional[str]:
        return self._vacancy_title

    @staticmethod
    def _build_page_url(url: str, page: int) -> str:
        """Returns URL with &page=N appended; returns original URL for page 0."""
        if page == 0:
            return url
        url_clean = re.sub(r'[&?]page=\d+', '', url)
        return f"{url_clean}&page={page}"

    @staticmethod
    def _extract_vacancy_id(url: str) -> Optional[str]:
        """Extracts numeric vacancy ID from any HH URL variant.

        Works on both canonical (hh.ru/vacancy/12345678) and relative (/vacancy/12345678).
        Returns None for tracking URLs (adsrv.hh.ru/click?...) that encode no vacancy ID.
        """
        if not url:
            return None
        m = re.search(r'/vacancy/(\d+)', url)
        return m.group(1) if m else None
        
    def start(self, debug: bool = False) -> bool:
        """Launches browser and loads cookies. Navigation happens in get_vacancy_urls()."""
        try:
            self._pw_manager = sync_playwright()
            self.playwright = self._pw_manager.start()
            # BROWSER_CORNER=true → small window bottom-right (monitor without blocking work).
            # Non-headless, non-corner, non-debug → offscreen (invisible real browser).
            # Debug without BROWSER_CORNER → full window at default position.
            launch_args = []
            if not CONFIG.headless:
                corner = os.getenv("BROWSER_CORNER", "false").lower() == "true"
                if corner:
                    launch_args = ["--window-size=750,430"]
                elif not debug:
                    launch_args = ["--window-position=-2000,-2000", "--window-size=1280,800"]
            self.browser = self.playwright.chromium.launch(
                headless=CONFIG.headless,
                args=launch_args + ["--disable-blink-features=AutomationControlled"],
            )
            # HH.ru detects plain Playwright/CDP automation (navigator.webdriver=true,
            # and headless mode's own "HeadlessChrome" UA string) and appears to
            # withhold authenticated content in response — confirmed live 2026-08-02:
            # a genuinely valid, fresh-cookie session showed zero resume links under
            # automation defaults, and immediately/correctly showed them once
            # navigator.webdriver was patched and the UA de-headlessed. Applied here
            # too, not just the liveness probe, since real runs hit the same fingerprint.
            self.context = self.browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            )
            self.context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            self._load_cookies()
            self.page = self.context.new_page()
            if not CONFIG.headless and corner:
                x = int(os.getenv("BROWSER_CORNER_X", "1578"))
                y = int(os.getenv("BROWSER_CORNER_Y", "650"))
                try:
                    cdp = self.context.new_cdp_session(self.page)
                    win_id = cdp.send("Browser.getWindowForTarget", {})["windowId"]
                    cdp.send("Browser.setWindowBounds", {
                        "windowId": win_id,
                        "bounds": {"left": x, "top": y, "width": 750, "height": 430},
                    })
                except Exception:
                    pass
            return True
        except Exception as e:
            self._say(f"❌ Browser launch error: {e}",
                      gui_message="Couldn't start the browser")
            self.close()
            return False
    
    def _load_cookies(self) -> None:
        """Loads cookies from file."""
        try:
            with open(CONFIG.cookies_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            self.context.add_cookies(cookies)
            self._say(f"✅ Cookies loaded from {CONFIG.cookies_path}",
                      gui_message="Signed in using your saved session")
        except Exception as e:
            self._say(f"⚠️ Cookies load error: {e}",
                      gui_message="Couldn't restore your saved session")
    
    def _close_cookie_modal(self) -> None:
        """Closes the cookie consent modal."""
        try:
            cookie_btn = self.page.wait_for_selector(
                SELECTORS['cookie_accept'],
                timeout=CONFIG.modal_wait
            )
            if cookie_btn:
                cookie_btn.click()
                self._say("   ✅ Cookie consent button clicked",
                          gui_message="Dismissed a cookie banner")
                self.page.wait_for_selector(
                    SELECTORS['cookie_accept'],
                    state='hidden',
                    timeout=CONFIG.modal_wait
                )
                # Plain print — folded into the "Dismissed a cookie banner"
                # line above for the GUI.
                print("   ✅ Cookie modal closed")
        except Exception:
            # `except:` until 2026-08-11, which also swallowed KeyboardInterrupt
            # and SystemExit — a Ctrl-C landing inside this wait was reported as
            # "cookie modal not found" and the run carried on.
            # Plain print — the common "nothing to dismiss" case, not worth a GUI line.
            print("   ⚠️ Cookie modal not found or already closed")

        self.page.wait_for_timeout(3000)
    
    @staticmethod
    def _search_source_label(search_url: str) -> str:
        """Returns a human-readable label for a search URL used as search_source in the log.

        wise link (contains resume= param) → "wise_link"
        text query (contains text= param)  → the query string, e.g. "product manager"
        anything else                       → "wise_link" (safe fallback)
        """
        params = parse_qs(urlparse(search_url).query)
        if 'resume' in params:
            return 'wise_link'
        text = params.get('text', [''])[0].strip()
        return text if text else 'wise_link'

    def _goto_with_retry(self, url: str, *, attempts: int = 2, retry_wait_ms: int = 2000, **goto_kwargs):
        """page.goto() with a small bounded retry on transient network errors.

        Observed live (2026-07-20): a single ERR_NETWORK_CHANGED (WiFi/VPN blip)
        during the search page load aborted an entire session with 0 vacancies
        found — the network condition that caused it was gone by the next
        attempt. Not a general-purpose retry: a persistent failure still
        propagates to the caller's own except block after the last attempt,
        same as before this existed.
        """
        for attempt in range(1, attempts + 1):
            try:
                return self.page.goto(url, **goto_kwargs)
            except Exception as e:
                if attempt >= attempts:
                    raise
                # Plain print — bounded internal retry, not worth a GUI line.
                print(f"   ⚠️ goto failed (attempt {attempt}/{attempts}): {e} — retrying in {retry_wait_ms}ms")
                self.page.wait_for_timeout(retry_wait_ms)

    def get_vacancy_urls(self, per_url_limit: int = 0, target_url: str = None) -> List[tuple]:
        """Visits all search URLs, returns deduplicated interleaved list of
        (url, title, index, search_source) tuples.

        per_url_limit > 0: collect at most N vacancies per URL, then interleave round-robin
        so each search angle is represented evenly in the processed queue.
        per_url_limit = 0: no cap, pool all vacancies sequentially (legacy behaviour).

        target_url set (single-vacancy debug mode, session 55): bypasses
        search_urls.txt and all scraping entirely — returns exactly one
        entry for this vacancy, no other vacancy can end up in the result.
        Explicit parameter rather than monkeypatching _load_search_urls (the
        original session-55 approach): that indirection was implicated in a
        real live-run incident where the override silently didn't take
        effect and normal search vacancies got processed/applied-to instead
        — an explicit parameter on the actual call path can't have that
        failure mode.
        """
        if target_url:
            import re as _re
            m = _re.search(r'/vacancy/(\d+)', target_url)
            if not m:
                self._say(f"❌ target_url doesn't look like a vacancy URL: {target_url}",
                          gui_message="That link doesn't look like a vacancy — stopping")
                return []
            vacancy_id = m.group(1)
            clean_url = f'https://hh.ru/vacancy/{vacancy_id}'
            self._say(f"🔹 Single-URL debug mode — exactly one vacancy: {clean_url}",
                      gui_message="Focusing on one specific vacancy")
            return [(clean_url, f'vacancy/{vacancy_id}', 1, 'direct')]

        search_urls = self._load_search_urls()
        if not search_urls:
            self._say("❌ No search URLs configured (run onboarding/wizard.py --block b)",
                      gui_message="No search set up yet")
            return []

        url_buckets: list = []  # one list per search URL
        source_labels: list = []  # parallel list: search_source label per bucket

        for i, search_url in enumerate(search_urls):
            source = self._search_source_label(search_url)

            # Direct vacancy URL — single-item bucket, no scraping
            if re.search(r'/vacancy/\d+', search_url):
                vacancy_id = re.search(r'/vacancy/(\d+)', search_url).group(1)
                clean_url = f'https://hh.ru/vacancy/{vacancy_id}'
                url_buckets.append([(clean_url, f'vacancy/{vacancy_id}', 1)])
                source_labels.append('direct')
                # Plain print — internal routing detail, the aggregate "Found
                # N vacancies" line below is what the GUI actually shows.
                print(f"🔹 Direct vacancy URL: {clean_url}")
                continue

            print(f"🔹 Search {i+1}/{len(search_urls)} [{source}]: {search_url[:80]}...")
            bucket: list = []
            try:
                for page_num in range(CONFIG.max_pages):
                    page_url = self._build_page_url(search_url, page_num)
                    self._goto_with_retry(page_url, timeout=CONFIG.page_load_timeout,
                                          wait_until="domcontentloaded")
                    # Geo-redirect check only on first page of each search URL
                    if page_num == 0:
                        actual_url = self.page.url
                        if '.hh.ru/' in actual_url and '://hh.ru/' not in actual_url:
                            canonical = re.sub(r'https://[\w-]+\.hh\.ru/', 'https://hh.ru/', actual_url)
                            print(f"   ⚠️ Geo-redirect detected → forcing hh.ru")
                            self._goto_with_retry(canonical, timeout=CONFIG.page_load_timeout,
                                                  wait_until="domcontentloaded")
                    # First page of the session: full wait for modals; subsequent pages: short wait
                    wait_ms = CONFIG.initial_wait if (i == 0 and page_num == 0) else 3000
                    print(f"⏳ Waiting {wait_ms/1000:.0f}s (page {page_num})...")
                    self.page.wait_for_timeout(wait_ms)
                    if i == 0 and page_num == 0:
                        self._close_cookie_modal()
                    self._scroll_to_load_all()
                    page_vacancies = self._scrape_vacancies()
                    if not page_vacancies:
                        print(f"   ⏹ Page {page_num}: empty — stopping pagination")
                        break
                    print(f"   📄 Page {page_num}: {len(page_vacancies)} vacancies")
                    bucket.extend(page_vacancies)
                    if per_url_limit > 0 and len(bucket) >= per_url_limit:
                        break
            except Exception as e:
                self._say(f"   ❌ Error loading search #{i+1}: {e}",
                          gui_message="Trouble loading one of your searches — trying the others")

            if per_url_limit > 0:
                bucket = bucket[:per_url_limit]
            url_buckets.append(bucket)
            source_labels.append(source)

        # Interleave buckets round-robin: URL1[0], URL2[0], ..., URL1[1], URL2[1], ...
        # This gives even coverage across all search angles when the run is cut short.
        seen: set = set()
        result: list = []
        for row in zip_longest(*url_buckets):
            for bucket_idx, item in enumerate(row):
                if item is None:
                    continue
                url, title, _ = item
                if url not in seen:
                    seen.add(url)
                    source = source_labels[bucket_idx] if bucket_idx < len(source_labels) else 'wise_link'
                    result.append((url, title, len(result) + 1, source))

        total_collected = sum(len(b) for b in url_buckets)
        limit_str = f"≤{per_url_limit}/URL" if per_url_limit > 0 else f"≤{CONFIG.max_pages} pages/URL"
        self._say(f"✅ Total vacancies: {len(result)} unique (from {total_collected} across {len(url_buckets)} URL(s), {limit_str})",
                  gui_message=f"Found {len(result)} vacancies to review")
        return result

    def _load_search_urls(self) -> List[str]:
        """Reads search URLs from the profile's own search_urls.txt, one per line.

        Profile directory ONLY — no fall back to a flat/global data dir. An
        earlier draft of this fix did fall back, and testing showed why that is
        wrong: a profile with no feed configured silently inherited a legacy
        global file and would have searched the wrong feed instead of failing.
        profiles.resolve_profile() already states the rule ("no fallback to a
        flat/legacy data dir in any branch — a profile is always required");
        this now matches it. The HH_SEARCH_URL env escape hatch below is
        unaffected: it is explicit, not a path guess.
        """
        path = self._data_dir / "search_urls.txt"
        if path.exists():
            return [u.strip() for u in path.read_text(encoding="utf-8").splitlines()
                    if u.strip() and not u.startswith('#')]
        # Backward-compat: old HH_SEARCH_URL env var
        import os
        fallback = os.getenv("HH_SEARCH_URL", "")
        if fallback:
            # Plain print — legacy/dev-config detail, not user narration.
            print("   ⚠️ search_urls.txt not found — using HH_SEARCH_URL from .env (legacy)")
            return [fallback]
        return []

    def _scroll_to_load_all(self) -> None:
        """Scrolls down until no new vacancy cards appear (HH lazy-loads within each page).

        HH renders ~20 cards on initial load; subsequent batches appear as the user scrolls.
        Stops as soon as two consecutive scroll steps yield the same count, or after 10 steps.
        """
        prev_count = 0
        for _ in range(10):
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.page.wait_for_timeout(800)
            curr_count = len(self.page.query_selector_all(SELECTORS['vacancy_title']))
            if curr_count == prev_count:
                break
            prev_count = curr_count

    def _scrape_vacancies(self) -> List[tuple]:
        """Scrapes vacancy links from the current search results page."""
        try:
            elements = self.page.query_selector_all(SELECTORS['vacancy_title'])
            vacancies = []
            for i, el in enumerate(elements):
                try:
                    url = el.get_attribute('href') or ""
                    if not url.startswith('http'):
                        url = 'https://hh.ru' + url
                    title = el.inner_text().strip()
                    vacancies.append((url, title, i + 1))
                except Exception as e:
                    # Plain print — one bad card among many, not worth a GUI line.
                    print(f"   ⚠️ Vacancy #{i+1} error: {e}")
            return vacancies
        except Exception as e:
            print(f"   ❌ Scraping error: {e}")
            return []
    
    def open_vacancy(self, url: str, index: int = None) -> bool:
        """Opens a vacancy in a new tab and captures canonical URL + vacancy ID.

        index: the run's per-vacancy sequence number (adapter.py's own
        `index`), stored for the duration of this vacancy so later calls
        (get_vacancy_text, click_apply_button) can tag their GUI events
        with it — see _vacancy_seq / _vacancy_gui_id() above.
        """
        self._canonical_url = None
        self._vacancy_id = None
        self._vacancy_title = None
        self._vacancy_seq = index
        try:
            self.vacancy_page = self.context.new_page()
            self.vacancy_page.goto(url, timeout=CONFIG.page_load_timeout, wait_until="domcontentloaded")
            self.vacancy_page.bring_to_front()
            title_el = self.vacancy_page.wait_for_selector(SELECTORS['vacancy_title_page'], timeout=30000)

            # Capture canonical URL after redirect (tracking URLs resolve to hh.ru/vacancy/ID)
            self._canonical_url = self.vacancy_page.url
            self._vacancy_id = self._extract_vacancy_id(self._canonical_url)
            # Real title from the page itself — single-URL/target_url mode
            # (session 55) has no scraped title to pass in at all (bypasses
            # search entirely), so the caller's own title was a hardcoded
            # 'vacancy/{id}' placeholder that ended up literally in History/
            # Dashboard's Position column. Reusing the element wait_for_selector
            # already found above costs nothing extra — no new page query.
            try:
                self._vacancy_title = title_el.inner_text().strip() or None
            except Exception:
                pass

            self._say("   ✅ Vacancy loaded",
                      gui_message="Vacancy opened",
                      vacancy_id=self._vacancy_gui_id(), position=self._vacancy_title)
            self._dismiss_cookie_banner(self.vacancy_page)
            return True

        except Exception as e:
            self._say(f"   ❌ Error opening vacancy: {e}",
                      gui_message="[BLCK] couldn't open this vacancy",
                      vacancy_id=self._vacancy_gui_id())
            return False

    def _dismiss_cookie_banner(self, page) -> None:
        """Silently closes cookie consent banner on any page (footer or modal)."""
        try:
            btn = find_visible(page, SELECTORS['cookie_accept'])
            if btn is not None:
                btn.click()
        except Exception:
            pass
    
    def get_employer_rating(self) -> Optional[float]:
        """Extracts employer review rating score from the open vacancy page.

        Selector confirmed from debug_screenshots (2026-04-05):
          [data-qa="company-review-rating-value"] → text "4.3"

        Returns float if found, None if the employer has no reviews on HH.ru.
        None should be treated as "unknown rating" — caller decides whether to skip.
        """
        el = find_visible(self.vacancy_page, SELECTORS['employer_rating'])
        if el is None:
            return None
        raw = ""
        try:
            raw = el.inner_text().strip().replace(",", ".")
            return float(raw)
        except Exception as e:
            # A parse failure is NOT "this employer has no reviews", which is
            # what a bare `return None` told every caller downstream — including
            # the LLM, which was handed "HH Employer Rating: no reviews on HH".
            print(f"   ⚠️ Employer rating found but unreadable ({raw!r}: {e}) — treating as unknown")
            return None

    def get_company_name(self) -> str:
        """Extracts employer/company name from the open vacancy page.

        Returns empty string if not found — caller treats that as 'unknown, skip check'.
        Tries primary selector first, falls back to secondary.
        """
        for el in iter_visible(self.vacancy_page, SELECTORS['company_name']):
            try:
                name = el.inner_text().strip()
                if name:
                    return name
            except Exception as e:
                print(f"   ⚠️ Company name element found but unreadable ({e}) — trying the next match")
                continue
        return ""

    def get_vacancy_text(self) -> Optional[str]:
        """Extracts vacancy description text.

        Every match is checked, not just the first in the DOM — the same
        correction click_apply_button() already carries, and for the same
        reason. Branded ("constructor") vacancy pages can render TWO elements
        carrying data-qa="vacancy-description": an empty wrapper followed by
        the one holding the actual copy. query_selector() returns the wrapper,
        inner_text() gives "", and the caller reports "Could not extract
        vacancy text" on a page whose description is sitting right there.

        Confirmed on a live vacancy page (2026-08-11): element [0] 0 chars,
        element [1] 3787 chars.
        """
        try:
            elements = self.vacancy_page.query_selector_all(SELECTORS['vacancy_description'])
            for desc_element in elements:
                try:
                    text = desc_element.inner_text()
                except Exception:
                    continue  # detached/unrenderable node — try the next match
                if text and text.strip():
                    # Plain print — folded into "Vacancy opened" for the GUI.
                    print(f"   ✅ Extracted {len(text)} characters of description")
                    return text

            if elements:
                # Present but all empty: a different failure from "not on the
                # page at all", and worth distinguishing in the log.
                self._say(f"   ⚠️ Vacancy description found ({len(elements)}) but all empty",
                          gui_message="This vacancy's description came back empty",
                          vacancy_id=self._vacancy_gui_id())
            else:
                self._say("   ⚠️ Vacancy description not found",
                          gui_message="Couldn't find a description on this page",
                          vacancy_id=self._vacancy_gui_id())
            return None

        except Exception as e:
            self._say(f"   ❌ Text extraction error: {e}",
                      gui_message="Couldn't read this vacancy's description",
                      vacancy_id=self._vacancy_gui_id())
            return None
    
    # Positive evidence that an Apply click landed. Any ONE of these means HH
    # accepted the click and put something in front of the user.
    # Addresses, not hashed class names: HH ships CSS-module classes like
    # magritte-modal-content-wrapper___-eFo3_1-0-14 whose hash changes on every
    # frontend deploy. The data-qa values below were read off a real captured
    # modal (captured 2026-08-11) and are design-system contract names.
    #
    # role="dialog"/alertdialog and magritte-alert are kept because adapter.py's
    # own modal handling already relies on them, but they are NOT sufficient on
    # their own — the real magritte response modal carries none of the three,
    # which is why this list is address-based and deliberately broad. A missed
    # signal here costs a false "nothing happened"; there is no action taken on
    # a match beyond continuing the normal flow, so breadth is the safe side.
    _RESPONSE_SURFACE = (
        '[data-qa="modal-header"]',
        '[data-qa="modal-footer"]',
        '[data-qa="modal-content-scroll-container"]',
        '[class*="magritte-modal"]',
        '[role="dialog"]',
        '[role="alertdialog"]',
        '[data-qa="magritte-alert"]',
        '[data-qa="vacancy-response-popup"]',
        '[data-qa="vacancy-response-link-view-topic"]',  # chat/topic opened
        'textarea',                                      # cover-letter form, modal or inline
    )

    def _apply_response_surface(self, before_url: str) -> bool:
        """Did the Apply click actually produce something?

        Replaces the previous test, which asked the opposite question — "is the
        button I clicked still sitting there, visible, with the same label?" —
        and treated yes as "the click never landed". That inverts on the single
        most common success path on hh.ru: the response MODAL opens *over* the
        page, leaving the original button exactly where it was. Playwright's
        is_visible() is about layout and CSS visibility, not occlusion, so a
        button underneath a modal still reports visible. The result was that a
        successful apply was declared a failure, a forced re-click was fired
        *through* the open modal, and the vacancy was logged as
        "skipped_no_apply_button" — while the modal the LLM loop was supposed to
        fill in sat open and unattended. Found live on a vacancy page,
        2026-08-11; introduced 2026-08-03 in the apply-loop robustness pass.

        Asking for positive evidence instead cannot fail that way: a modal, a
        cover-letter field, an opened chat topic, or a navigation are all things
        that exist only *because* the click worked.
        """
        try:
            if self.vacancy_page.url != before_url:
                return True
        except Exception:
            return True  # page/context replaced under us — it did something

        for selector in self._RESPONSE_SURFACE:
            try:
                for el in self.vacancy_page.query_selector_all(selector):
                    if el.is_visible():
                        return True
            except Exception:
                continue
        return False

    def click_apply_button(self) -> bool:
        """Clicks the 'Apply' button."""
        try:
            apply_button = None
            # Poll up to ~5s (some vacancy pages are slow to render) and check
            # EVERY match per selector, not just the first DOM hit — a vacancy
            # page commonly has multiple "Откликнуться" elements (recommendation
            # cards below the fold), and the old query_selector() (single-match)
            # gave up if that specific first-in-DOM element happened to be
            # invisible, even when later matches for the same selector were
            # visible (found live 2026-08-02 — 4 matches for
            # a:has-text("Откликнуться"), 3 of them visible, but the check still
            # reported "not found").
            for _ in range(10):
                for selector in SELECTORS['apply_button']:
                    for candidate in self.vacancy_page.query_selector_all(selector):
                        if candidate.is_visible():
                            apply_button = candidate
                            # Plain print — internal selector detail.
                            print(f"   ✅ Found 'Apply' button: {selector}")
                            break
                    if apply_button:
                        break
                if apply_button:
                    break
                self.vacancy_page.wait_for_timeout(500)

            # Fallback: search by button text
            if not apply_button:
                all_buttons = self.vacancy_page.query_selector_all('button, a')
                for btn in all_buttons:
                    try:
                        text = btn.inner_text().strip().lower()
                        if 'отклик' in text and btn.is_visible():
                            apply_button = btn
                            print(f"   ✅ Found button by text: '{btn.inner_text().strip()}'")
                            break
                    except:
                        continue

            if not apply_button:
                self._say("   ❌ 'Apply' button not found",
                          gui_message="[BLCK] couldn't find the Apply button",
                          vacancy_id=self._vacancy_gui_id())
                return False

            before_url = self.vacancy_page.url
            apply_button.click()
            # Plain print — adapter.py already announced "Clicking Apply…"
            # right before this call; this would just repeat it.
            print("   ✅ 'Apply' button clicked")

            # Human-like pause for the form to appear
            time.sleep(7)

            if not self._apply_response_surface(before_url):
                # Nothing appeared. NOW the overlay-intercept theory is worth
                # acting on — a forced click bypasses actionability checks, so
                # it must only ever fire when we are confident nothing is
                # layered on top to receive it.
                print("   ⚠️ 'Apply' click produced no response surface — retrying with a forced click")
                try:
                    apply_button.click(force=True)
                    time.sleep(7)
                except Exception as e:
                    print(f"   ⚠️ Forced click failed: {e}")
                if not self._apply_response_surface(before_url):
                    self._say("   ❌ 'Apply' click produced no response form, modal or navigation",
                              gui_message="[BLCK] the Apply click didn't seem to register",
                              vacancy_id=self._vacancy_gui_id())
                    return False

            return True

        except Exception as e:
            self._say(f"   ❌ Error clicking 'Apply' button: {e}",
                      gui_message="Ran into trouble clicking Apply",
                      vacancy_id=self._vacancy_gui_id())
            return False
    
    def close_vacancy(self) -> None:
        """Closes the vacancy tab."""
        if self.vacancy_page:
            self.vacancy_page.close()
            self.vacancy_page = None
            self.page.bring_to_front()
            time.sleep(3)
        # Matches _vacancy_gui_id()'s own docstring (session 58 code-review
        # catch): without this, any browser-level event narrated between
        # this call and the next open_vacancy() would silently mis-attribute
        # to the vacancy that just closed instead of showing no vacancy.
        self._vacancy_seq = None
    
    def close(self) -> None:
        """Closes browser and Playwright driver. Idempotent — safe to call multiple times."""
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self._pw_manager:
            try:
                self._pw_manager.__exit__(None, None, None)
            except Exception:
                pass
            self._pw_manager = None
            self.playwright = None
    
    def wait_for_timeout(self, ms: int) -> None:
        """Waits for the given duration in milliseconds."""
        if self.vacancy_page:
            self.vacancy_page.wait_for_timeout(ms)
        elif self.page:
            self.page.wait_for_timeout(ms)
    
    def get_current_page(self):
        """Returns the currently active page."""
        return self.vacancy_page if self.vacancy_page else self.page