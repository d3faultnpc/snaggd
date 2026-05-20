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
        """Запускает браузер и загружает HH"""
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=CONFIG.headless)
            self.context = self.browser.new_context()
            
            # Загружаем cookies
            self._load_cookies()
            
            self.page = self.context.new_page()
            
            print("🔹 Открываем HH.ru...")
            self.page.goto(
                CONFIG.hh_search_url,
                timeout=CONFIG.page_load_timeout,
                wait_until="domcontentloaded"
            )
            
            # Ждём первоначальную загрузку
            print(f"⏳ Ждём {CONFIG.initial_wait/1000} сек (загрузка + модалки)...")
            self.page.wait_for_timeout(CONFIG.initial_wait)
            
            # Закрываем cookie модалку
            self._close_cookie_modal()
            
            return True
            
        except Exception as e:
            print(f"❌ Ошибка запуска браузера: {e}")
            return False
    
    def _load_cookies(self) -> None:
        """Загружает cookies из файла"""
        try:
            with open(CONFIG.cookies_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            self.context.add_cookies(cookies)
            print(f"✅ Загружены cookies из {CONFIG.cookies_path}")
        except Exception as e:
            print(f"⚠️ Ошибка загрузки cookies: {e}")
    
    def _close_cookie_modal(self) -> None:
        """Закрывает модалку с согласием на cookies"""
        try:
            cookie_btn = self.page.wait_for_selector(
                SELECTORS['cookie_accept'], 
                timeout=CONFIG.modal_wait
            )
            if cookie_btn:
                cookie_btn.click()
                print("   ✅ Кнопка 'Понятно' нажата")
                
                # Ждём исчезновения модалки
                self.page.wait_for_selector(
                    SELECTORS['cookie_accept'], 
                    state='hidden', 
                    timeout=CONFIG.modal_wait
                )
                print("   ✅ Cookie модалка закрыта")
        except:
            print("   ⚠️ Cookie модалка не найдена или уже закрыта")
        
        # Дополнительная пауза
        self.page.wait_for_timeout(3000)
    
    def get_vacancy_urls(self) -> List[tuple]:
        """Получает список URL вакансий с заголовками"""
        try:
            vacancy_elements = self.page.query_selector_all(SELECTORS['vacancy_title'])
            vacancies = []
            
            for i, element in enumerate(vacancy_elements):
                try:
                    url = element.get_attribute('href')
                    title = element.inner_text().strip()
                    
                    if not url.startswith('http'):
                        url = 'https://hh.ru' + url
                    
                    vacancies.append((url, title, i+1))
                    
                except Exception as e:
                    print(f"   ⚠️ Ошибка получения вакансии #{i+1}: {e}")
                    continue
            
            print(f"✅ Найдено {len(vacancies)} вакансий")
            return vacancies
            
        except Exception as e:
            print(f"❌ Ошибка получения списка вакансий: {e}")
            return []
    
    def open_vacancy(self, url: str) -> bool:
        """Открывает вакансию в новой вкладке"""
        try:
            self.vacancy_page = self.context.new_page()
            self.vacancy_page.goto(url, timeout=CONFIG.page_load_timeout, wait_until="domcontentloaded")
            self.vacancy_page.bring_to_front()
            
            # Ждём загрузки заголовка вакансии
            self.vacancy_page.wait_for_selector(SELECTORS['vacancy_title_page'], timeout=30000)
            print("   ✅ Вакансия загружена")
            
            return True
            
        except Exception as e:
            print(f"   ❌ Ошибка открытия вакансии: {e}")
            return False
    
    def get_vacancy_text(self) -> Optional[str]:
        """Извлекает текст описания вакансии"""
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
        """Кликает кнопку 'Откликнуться'"""
        try:
            # Ищем кнопку по списку селекторов
            apply_button = None
            for selector in SELECTORS['apply_button']:
                button = self.vacancy_page.query_selector(selector)
                if button and button.is_visible():
                    apply_button = button
                    print(f"   ✅ Найдена кнопка 'Откликнуться': {selector}")
                    break
            
            # Fallback поиск по тексту
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
            
            # Кликаем
            apply_button.click()
            print("   ✅ Кнопка 'Откликнуться' нажата")
            
            # Ждём появления формы
            time.sleep(3 + 4)  # 3-7 секунд человеческая пауза
            
            return True
            
        except Exception as e:
            print(f"   ❌ Ошибка клика кнопки 'Откликнуться': {e}")
            return False
    
    def close_vacancy(self) -> None:
        """Закрывает вкладку с вакансией"""
        if self.vacancy_page:
            self.vacancy_page.close()
            self.vacancy_page = None
            
            # Возвращаемся на основную страницу
            self.page.bring_to_front()
            time.sleep(3)
    
    def close(self) -> None:
        """Закрывает браузер"""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    def wait_for_timeout(self, ms: int) -> None:
        """Ждёт указанное время"""
        if self.vacancy_page:
            self.vacancy_page.wait_for_timeout(ms)
        elif self.page:
            self.page.wait_for_timeout(ms)
    
    def get_current_page(self):
        """Возвращает текущую активную страницу"""
        return self.vacancy_page if self.vacancy_page else self.page