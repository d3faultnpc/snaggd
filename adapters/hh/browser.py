import json
import time
from typing import List, Optional
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from config import CONFIG, SELECTORS

class HHBrowser:
    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.vacancy_page: Optional[Page] = None
        
    def start(self) -> bool:
        """Launches browser and loads cookies. Navigation happens in get_vacancy_urls()."""
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=CONFIG.headless)
            self.context = self.browser.new_context()
            self._load_cookies()
            self.page = self.context.new_page()
            return True
        except Exception as e:
            print(f"❌ Ошибка запуска браузера: {e}")
            return False
    
    def _load_cookies(self) -> None:
        """Loads cookies from file."""
        try:
            with open(CONFIG.cookies_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            self.context.add_cookies(cookies)
            print(f"✅ Загружены cookies из {CONFIG.cookies_path}")
        except Exception as e:
            print(f"⚠️ Ошибка загрузки cookies: {e}")
    
    def _close_cookie_modal(self) -> None:
        """Closes the cookie consent modal."""
        try:
            cookie_btn = self.page.wait_for_selector(
                SELECTORS['cookie_accept'],
                timeout=CONFIG.modal_wait
            )
            if cookie_btn:
                cookie_btn.click()
                print("   ✅ Кнопка 'Понятно' нажата")
                self.page.wait_for_selector(
                    SELECTORS['cookie_accept'],
                    state='hidden',
                    timeout=CONFIG.modal_wait
                )
                print("   ✅ Cookie модалка закрыта")
        except:
            print("   ⚠️ Cookie модалка не найдена или уже закрыта")

        self.page.wait_for_timeout(3000)
    
    def get_vacancy_urls(self) -> List[tuple]:
        """Visits all search URLs, returns deduplicated list of (url, title, index)."""
        search_urls = self._load_search_urls()
        if not search_urls:
            print("❌ No search URLs configured (run onboarding/wizard.py --block b)")
            return []

        seen: set = set()
        all_vacancies: list = []

        for i, search_url in enumerate(search_urls):
            print(f"🔹 Search {i+1}/{len(search_urls)}: {search_url[:80]}...")
            try:
                self.page.goto(search_url, timeout=CONFIG.page_load_timeout,
                               wait_until="domcontentloaded")
                print(f"⏳ Ждём {CONFIG.initial_wait/1000} сек (загрузка + модалки)...")
                self.page.wait_for_timeout(CONFIG.initial_wait)
                if i == 0:
                    self._close_cookie_modal()
                all_vacancies.extend(self._scrape_vacancies())
            except Exception as e:
                print(f"   ❌ Ошибка загрузки поиска #{i+1}: {e}")
                continue

        # Deduplicate by URL, reassign sequential index
        result = []
        for url, title, _ in all_vacancies:
            if url not in seen:
                seen.add(url)
                result.append((url, title, len(result) + 1))

        print(f"✅ Итого вакансий: {len(result)} (из {len(all_vacancies)} по {len(search_urls)} поискам)")
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
                    print(f"   ⚠️ Ошибка вакансии #{i+1}: {e}")
            return vacancies
        except Exception as e:
            print(f"   ❌ Ошибка скрейпинга: {e}")
            return []
    
    def open_vacancy(self, url: str) -> bool:
        """Opens a vacancy in a new tab."""
        try:
            self.vacancy_page = self.context.new_page()
            self.vacancy_page.goto(url, timeout=CONFIG.page_load_timeout, wait_until="domcontentloaded")
            self.vacancy_page.bring_to_front()
            self.vacancy_page.wait_for_selector(SELECTORS['vacancy_title_page'], timeout=30000)
            print("   ✅ Вакансия загружена")
            
            return True
            
        except Exception as e:
            print(f"   ❌ Ошибка открытия вакансии: {e}")
            return False
    
    def get_vacancy_text(self) -> Optional[str]:
        """Extracts vacancy description text."""
        try:
            desc_element = self.vacancy_page.query_selector(SELECTORS['vacancy_description'])
            if desc_element:
                text = desc_element.inner_text()
                print(f"   ✅ Извлечено {len(text)} символов описания")
                return text
            else:
                print("   ⚠️ Описание вакансии не найдено")
                return None
                
        except Exception as e:
            print(f"   ❌ Ошибка извлечения текста: {e}")
            return None
    
    def click_apply_button(self) -> bool:
        """Clicks the 'Apply' button."""
        try:
            apply_button = None
            for selector in SELECTORS['apply_button']:
                button = self.vacancy_page.query_selector(selector)
                if button and button.is_visible():
                    apply_button = button
                    print(f"   ✅ Найдена кнопка 'Откликнуться': {selector}")
                    break

            # Fallback: search by button text
            if not apply_button:
                all_buttons = self.vacancy_page.query_selector_all('button, a')
                for btn in all_buttons:
                    try:
                        text = btn.inner_text().strip().lower()
                        if 'отклик' in text and btn.is_visible():
                            apply_button = btn
                            print(f"   ✅ Найдена кнопка по тексту: '{btn.inner_text().strip()}'")
                            break
                    except:
                        continue

            if not apply_button:
                print("   ❌ Кнопка 'Откликнуться' не найдена")
                return False

            apply_button.click()
            print("   ✅ Кнопка 'Откликнуться' нажата")

            # Human-like pause for the form to appear
            time.sleep(3 + 4)
            
            return True
            
        except Exception as e:
            print(f"   ❌ Ошибка клика кнопки 'Откликнуться': {e}")
            return False
    
    def close_vacancy(self) -> None:
        """Closes the vacancy tab."""
        if self.vacancy_page:
            self.vacancy_page.close()
            self.vacancy_page = None
            self.page.bring_to_front()
            time.sleep(3)
    
    def close(self) -> None:
        """Closes the browser."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    def wait_for_timeout(self, ms: int) -> None:
        """Waits for the given duration in milliseconds."""
        if self.vacancy_page:
            self.vacancy_page.wait_for_timeout(ms)
        elif self.page:
            self.page.wait_for_timeout(ms)
    
    def get_current_page(self):
        """Returns the currently active page."""
        return self.vacancy_page if self.vacancy_page else self.page