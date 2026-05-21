#!/usr/bin/env python3
"""
HH Auto - модульная версия автоматизации откликов

Использование:
    python main.py                        # обычный режим
    python main.py --debug                # debug: скриншоты + HTML на каждом шаге
    python main.py --debug --max 3        # debug на 3 вакансиях
    python main.py --search-url "https://hh.ru/search/vacancy?..."  # другой поиск
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from config import CONFIG, SELECTORS
from logger import Logger
from browser import HHBrowser
from form_detector import FormDetector
from form_handlers import FormHandlers
from llm_cover import LLMCover
from hr_matcher import HRMatcher
from utils.helpers import random_delay

DEBUG_DIR = Path(os.getenv("DEBUG_DIR", Path(__file__).parent / "debug_screenshots"))


# ---------------------------------------------------------------------------
# Debug helpers
# ---------------------------------------------------------------------------

def debug_snapshot(page, session_dir: Path, label: str) -> None:
    """Сохраняет скриншот + HTML в папку сессии."""
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = session_dir / f"{label}.png"
        html_path = session_dir / f"{label}.html"

        page.screenshot(path=str(screenshot_path), full_page=False)

        # HTML только модалки или body
        modal = None
        for sel in ['[role="dialog"]', '[data-qa*="modal"]', '[data-qa*="response"]', '.HH-Modal']:
            el = page.query_selector(sel)
            if el and el.is_visible():
                modal = el
                break
        html_content = modal.inner_html() if modal else page.inner_html('body')
        html_path.write_text(html_content, encoding="utf-8")

        # data-qa атрибуты
        data_qa = page.evaluate("""() => {
            const els = document.querySelectorAll('[data-qa]');
            const vals = new Set();
            els.forEach(el => vals.add(el.getAttribute('data-qa')));
            return Array.from(vals).sort();
        }""")
        (session_dir / f"{label}_data_qa.txt").write_text("\n".join(data_qa), encoding="utf-8")

        print(f"   📸 [{label}] скриншот + HTML + {len(data_qa)} data-qa → {session_dir.name}/")
    except Exception as e:
        print(f"   ⚠️ debug_snapshot [{label}] ошибка: {e}")


# ---------------------------------------------------------------------------
# Core vacancy processing
# ---------------------------------------------------------------------------

def process_vacancy(browser, detector, handlers, llm_cover, hr_matcher,
                    url, title, index, debug: bool = False, session_dir: Path = None):
    """Обрабатывает одну вакансию."""

    try:
        if not browser.open_vacancy(url):
            return {'status': 'skipped_open_error', 'reason': 'Ошибка открытия вакансии'}

        delay = random_delay(15000, 25000)
        print(f"   ⏳ Пауза {delay/1000:.1f}с (чтение вакансии)")

        if debug and session_dir:
            debug_snapshot(browser.get_current_page(), session_dir, "01_vacancy_page")

        vacancy_text = browser.get_vacancy_text()
        if not vacancy_text:
            return {'status': 'skipped_no_text', 'reason': 'Не удалось извлечь текст вакансии'}

        # Кликаем "Откликнуться" — ПЕРЕД генерацией сопроводительного
        print("   🔹 Кликаю 'Откликнуться'...")
        if not browser.click_apply_button():
            return {'status': 'skipped_no_apply_button', 'reason': 'Кнопка откликнуться не найдена'}

        if debug and session_dir:
            debug_snapshot(browser.get_current_page(), session_dir, "02_after_apply_click")

        # Проверяем мгновенную отправку (без формы)
        current_page = browser.get_current_page()
        try:
            success_notif = current_page.query_selector(SELECTORS['immediate_success'])
            if success_notif and success_notif.is_visible():
                print("   ✅ Отклик отправлен мгновенно (без формы)")
                return {
                    'status': 'applied_immediate',
                    'reason': 'Резюме отправлено без формы',
                    'scenario': 'immediate',
                    'details': {}
                }
        except Exception:
            pass

        # Детектируем тип формы
        print("   🔹 Анализирую форму отклика...")
        form_info = detector.detect(current_page)

        print(f"   📋 Тип формы: {form_info.form_type.value}")
        print(f"   📊 Полей: {form_info.input_count}, ЗП: {form_info.has_salary_field}")

        # Пропускаем зарплатные и unknown формы — не тратим токены LLM
        from form_handlers.base import FormType
        if form_info.form_type in (FormType.SALARY_FORM, FormType.UNKNOWN):  # TEST_FORM обрабатывается хендлером
            if debug and session_dir:
                debug_snapshot(current_page, session_dir, f"03_skip_{form_info.form_type.value}")
            return {
                'status': f'skipped_{form_info.form_type.value}',
                'reason': f'Форма пропущена: {form_info.form_type.value}',
                'scenario': 'skip'
            }

        # Генерируем сопроводительное только если форма нужна
        print("   🔹 Генерирую сопроводительное...")
        cover_letter, template_name, signals = llm_cover.generate(vacancy_text)
        match_score = llm_cover.last_score
        print(f"   📊 Шаблон: {template_name}, score: {match_score}, сигналы: {', '.join(signals) if signals else 'нет'}")

        # Обрабатываем форму
        handler = handlers.get_handler(form_info.form_type)
        result = handler.process(current_page, cover_letter, hr_matcher)

        if debug and session_dir:
            debug_snapshot(current_page, session_dir, f"03_after_handler_{result.status}")

        return {
            'status': result.status,
            'reason': result.reason,
            'scenario': result.scenario,
            'details': {
                'form_type': form_info.form_type.value,
                'template_name': template_name,
                'match_score': match_score,
                'signals': signals,
                **(result.details or {})
            }
        }

    except Exception as e:
        if debug and session_dir:
            try:
                debug_snapshot(browser.get_current_page(), session_dir, "error")
            except Exception:
                pass
        return {
            'status': 'skipped_error',
            'reason': f'Ошибка обработки: {str(e)}',
            'scenario': 'error'
        }

    finally:
        browser.close_vacancy()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="HH Auto")
    parser.add_argument("--debug", action="store_true",
                        help="Режим отладки: скриншоты + HTML дампы на каждом шаге")
    parser.add_argument("--max", type=int, default=None,
                        help="Максимум вакансий за сессию (переопределяет config)")
    parser.add_argument("--search-url", type=str, default=None,
                        help="URL поиска вакансий (переопределяет config)")
    args = parser.parse_args()

    if args.max:
        CONFIG.max_vacancies_per_session = args.max
    if args.search_url:
        CONFIG.hh_search_url = args.search_url

    debug = args.debug
    session_dir = None

    if debug:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir_base = DEBUG_DIR / f"session_{ts}"
        session_dir_base.mkdir(parents=True, exist_ok=True)
        print(f"🐛 DEBUG режим — снимки в: {session_dir_base}")

    print("🦾 HH Auto - модульная версия")
    print(f"📊 Лимиты: {CONFIG.max_vacancies_per_session} вакансий, {CONFIG.max_skips} пропусков")

    logger = Logger()
    browser = HHBrowser()
    detector = FormDetector()
    handlers = FormHandlers()
    llm_cover = LLMCover()
    hr_matcher = HRMatcher()

    applied_log = logger.load_applied_log()
    initial_log_count = len(applied_log)
    print(f"📄 Загружен applied_log: {initial_log_count} записей")

    processed_count = 0
    skip_count = 0

    try:
        if not browser.start():
            print("❌ Не удалось запустить браузер")
            return 1

        vacancies = browser.get_vacancy_urls()
        if not vacancies:
            print("❌ Не найдено вакансий")
            return 1

        print(f"✅ Найдено {len(vacancies)} вакансий")

        for url, title, index in vacancies:
            if processed_count >= CONFIG.max_vacancies_per_session:
                print(f"⏹ Достигнут лимит: {processed_count} вакансий")
                break

            if skip_count >= CONFIG.max_skips:
                print(f"⏹ Достигнут лимит пропусков: {skip_count}")
                break

            existing_status = logger.is_processed(url, applied_log)
            if existing_status:
                print(f"⏭ Вакансия #{index} уже обработана ({existing_status})")
                skip_count += 1
                continue

            print(f"\n{'='*50}")
            print(f"ВАКАНСИЯ #{index}: {title}")
            print(f"URL: {url}")
            logger.log_daily(f"ВАКАНСИЯ #{index}: {title}")
            logger.log_daily(f"URL: {url}")

            # Папка для debug снимков этой вакансии
            vac_debug_dir = None
            if debug:
                safe_title = "".join(c for c in title[:30] if c.isalnum() or c in " _-").strip()
                vac_debug_dir = session_dir_base / f"{index:02d}_{safe_title}"

            result = process_vacancy(
                browser, detector, handlers, llm_cover, hr_matcher,
                url, title, index,
                debug=debug, session_dir=vac_debug_dir
            )

            logger.log_result(
                applied_log,
                url=url,
                title=title,
                status=result['status'],
                reason=result['reason'],
                scenario=result.get('scenario', 'unknown'),
                **result.get('details', {})
            )

            processed_count += 1
            logger.log_daily(f"Результат: {result['status']} - {result['reason']}")
            print(f"📊 Статус: {result['status']} - {result['reason']}")
            print(f"📈 Прогресс: {processed_count}/{CONFIG.max_vacancies_per_session}")

    except KeyboardInterrupt:
        print("\n⏹ Прервано пользователем")

    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        return 1

    finally:
        browser.close()

        successful, skipped = logger.count_session_results(applied_log, initial_log_count)
        new_entries = applied_log[initial_log_count:]

        print(f"\n{'='*50}")
        print(f"ИТОГИ СЕССИИ")
        print(f"Обработано: {processed_count}/{CONFIG.max_vacancies_per_session}")
        print(f"Успешных откликов: {successful}")
        print(f"Пропущено: {skipped}")
        print(f"Новых записей: {len(new_entries)}")

        logger.log_session_summary(processed_count, successful, skipped, new_entries)

        print(f"📄 applied_log: {CONFIG.applied_log_path}")
        print(f"📄 daily log: {logger.daily_log_path}")

        if debug:
            print(f"\n🐛 Debug снимки: {session_dir_base}")
            print(f"   Открой папку и скинь мне скриншоты для анализа")

    return 0


if __name__ == "__main__":
    sys.exit(main())
