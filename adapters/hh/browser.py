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

class HHBrowser:
    def __init__(self):
        self.playwright = None
        self._pw_manager = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.vacancy_page: Optional[Page] = None
        self._canonical_url: Optional[str] = None
        self._vacancy_id: Optional[str] = None
        atexit.register(self.close)

    @property
    def canonical_url(self) -> Optional[str]:
        return self._canonical_url

    @property
    def vacancy_id(self) -> Optional[str]:
        return self._vacancy_id

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
                args=launch_args,
            )
            self.context = self.browser.new_context()
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
            print(f"❌ Browser launch error: {e}")
            self.close()
            return False
    
    def _load_cookies(self) -> None:
        """Loads cookies from file."""
        try:
            with open(CONFIG.cookies_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            self.context.add_cookies(cookies)
            print(f"✅ Cookies loaded from {CONFIG.cookies_path}")
        except Exception as e:
            print(f"⚠️ Cookies load error: {e}")
    
    def _close_cookie_modal(self) -> None:
        """Closes the cookie consent modal."""
        try:
            cookie_btn = self.page.wait_for_selector(
                SELECTORS['cookie_accept'],
                timeout=CONFIG.modal_wait
            )
            if cookie_btn:
                cookie_btn.click()
                print("   ✅ Cookie consent button clicked")
                self.page.wait_for_selector(
                    SELECTORS['cookie_accept'],
                    state='hidden',
                    timeout=CONFIG.modal_wait
                )
                print("   ✅ Cookie modal closed")
        except:
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

    def get_vacancy_urls(self, per_url_limit: int = 0) -> List[tuple]:
        """Visits all search URLs, returns deduplicated interleaved list of
        (url, title, index, search_source) tuples.

        per_url_limit > 0: collect at most N vacancies per URL, then interleave round-robin
        so each search angle is represented evenly in the processed queue.
        per_url_limit = 0: no cap, pool all vacancies sequentially (legacy behaviour).
        """
        search_urls = self._load_search_urls()
        if not search_urls:
            print("❌ No search URLs configured (run onboarding/wizard.py --block b)")
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
                print(f"🔹 Direct vacancy URL: {clean_url}")
                continue

            print(f"🔹 Search {i+1}/{len(search_urls)} [{source}]: {search_url[:80]}...")
            bucket: list = []
            try:
                for page_num in range(CONFIG.max_pages):
                    page_url = self._build_page_url(search_url, page_num)
                    self.page.goto(page_url, timeout=CONFIG.page_load_timeout,
                                   wait_until="domcontentloaded")
                    # Geo-redirect check only on first page of each search URL
                    if page_num == 0:
                        actual_url = self.page.url
                        if '.hh.ru/' in actual_url and '://hh.ru/' not in actual_url:
                            canonical = re.sub(r'https://[\w-]+\.hh\.ru/', 'https://hh.ru/', actual_url)
                            print(f"   ⚠️ Geo-redirect detected → forcing hh.ru")
                            self.page.goto(canonical, timeout=CONFIG.page_load_timeout,
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
                print(f"   ❌ Error loading search #{i+1}: {e}")

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
        print(f"✅ Total vacancies: {len(result)} unique (from {total_collected} across {len(url_buckets)} URL(s), {limit_str})")
        return result

    def _load_search_urls(self) -> List[str]:
        """Reads search URLs from data/search_urls.txt, one per line."""
        path = CONFIG.search_urls_path
        if path.exists():
            return [u.strip() for u in path.read_text(encoding="utf-8").splitlines()
                    if u.strip() and not u.startswith('#')]
        # Backward-compat: old HH_SEARCH_URL env var
        import os
        fallback = os.getenv("HH_SEARCH_URL", "")
        if fallback:
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
                    print(f"   ⚠️ Vacancy #{i+1} error: {e}")
            return vacancies
        except Exception as e:
            print(f"   ❌ Scraping error: {e}")
            return []
    
    def open_vacancy(self, url: str) -> bool:
        """Opens a vacancy in a new tab and captures canonical URL + vacancy ID."""
        self._canonical_url = None
        self._vacancy_id = None
        try:
            self.vacancy_page = self.context.new_page()
            self.vacancy_page.goto(url, timeout=CONFIG.page_load_timeout, wait_until="domcontentloaded")
            self.vacancy_page.bring_to_front()
            self.vacancy_page.wait_for_selector(SELECTORS['vacancy_title_page'], timeout=30000)

            # Capture canonical URL after redirect (tracking URLs resolve to hh.ru/vacancy/ID)
            self._canonical_url = self.vacancy_page.url
            self._vacancy_id = self._extract_vacancy_id(self._canonical_url)

            print("   ✅ Vacancy loaded")
            self._dismiss_cookie_banner(self.vacancy_page)
            return True

        except Exception as e:
            print(f"   ❌ Error opening vacancy: {e}")
            return False

    def _dismiss_cookie_banner(self, page) -> None:
        """Silently closes cookie consent banner on any page (footer or modal)."""
        try:
            btn = page.query_selector(SELECTORS['cookie_accept'])
            if btn and btn.is_visible():
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
        try:
            el = self.vacancy_page.query_selector(SELECTORS['employer_rating'])
            if el and el.is_visible():
                raw = el.inner_text().strip().replace(",", ".")
                return float(raw)
        except Exception:
            pass
        return None

    def get_company_name(self) -> str:
        """Extracts employer/company name from the open vacancy page.

        Returns empty string if not found — caller treats that as 'unknown, skip check'.
        Tries primary selector first, falls back to secondary.
        """
        for selector in (SELECTORS['company_name'], SELECTORS['company_name_fallback']):
            try:
                el = self.vacancy_page.query_selector(selector)
                if el and el.is_visible():
                    name = el.inner_text().strip()
                    if name:
                        return name
            except Exception:
                continue
        return ""

    def get_vacancy_text(self) -> Optional[str]:
        """Extracts vacancy description text."""
        try:
            desc_element = self.vacancy_page.query_selector(SELECTORS['vacancy_description'])
            if desc_element:
                text = desc_element.inner_text()
                print(f"   ✅ Extracted {len(text)} characters of description")
                return text
            else:
                print("   ⚠️ Vacancy description not found")
                return None

        except Exception as e:
            print(f"   ❌ Text extraction error: {e}")
            return None
    
    def click_apply_button(self) -> bool:
        """Clicks the 'Apply' button."""
        try:
            apply_button = None
            for selector in SELECTORS['apply_button']:
                button = self.vacancy_page.query_selector(selector)
                if button and button.is_visible():
                    apply_button = button
                    print(f"   ✅ Found 'Apply' button: {selector}")
                    break

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
                print("   ❌ 'Apply' button not found")
                return False

            apply_button.click()
            print("   ✅ 'Apply' button clicked")

            # Human-like pause for the form to appear
            time.sleep(7)
            
            return True
            
        except Exception as e:
            print(f"   ❌ Error clicking 'Apply' button: {e}")
            return False
    
    def close_vacancy(self) -> None:
        """Closes the vacancy tab."""
        if self.vacancy_page:
            self.vacancy_page.close()
            self.vacancy_page = None
            self.page.bring_to_front()
            time.sleep(3)
    
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