from typing import Optional
from form_handlers.base import FormType, FormInfo
from config import SELECTORS, FORM_KEYWORDS

class FormDetector:
    """Детектор типов форм без использования LLM"""
    
    def detect(self, page) -> FormInfo:
        """Определяет тип формы по состоянию DOM"""
        
        # Базовый анализ элементов
        inputs = page.query_selector_all(SELECTORS['inputs_all'])
        labels = page.query_selector_all(SELECTORS['labels'])
        buttons = page.query_selector_all(SELECTORS['buttons'])
        
        # Извлекаем тексты
        all_labels_text = self._extract_visible_text(labels)
        all_buttons_text = self._extract_visible_text(buttons)
        all_placeholders = self._extract_placeholders(inputs)
        
        # Объединённый текст для анализа
        combined_text = f"{all_labels_text} {all_buttons_text} {all_placeholders}".lower()
        
        # Создаём базовую информацию
        info = FormInfo(
            form_type=FormType.UNKNOWN,
            input_count=len([inp for inp in inputs if inp.is_visible()]),
            labels=[label.inner_text().strip() for label in labels if label.is_visible()],
            placeholders=all_placeholders.split(),
            buttons=[btn.inner_text().strip() for btn in buttons if btn.is_visible()]
        )
        
        # Анализ прогресса
        progress_info = self._analyze_progress(page)
        info.has_progress = progress_info['has_progress']
        info.progress_step = progress_info['step']
        
        # Анализ типов полей
        info.has_salary_field = self._has_keywords(combined_text, FORM_KEYWORDS['salary'])
        info.has_cover_field = self._has_keywords(combined_text, FORM_KEYWORDS['cover'])

        # Детекция ссылки "Чат" через верифицированный data-qa (2026-04-05)
        try:
            chat_el = page.query_selector('[data-qa="vacancy-response-link-view-topic"]')
            info.has_chat_link = bool(chat_el and chat_el.is_visible())
        except Exception:
            info.has_chat_link = False
        
        # Детекция вопросов работодателя в попапе (vacancy-response-question, верифицировано 2026-04-06)
        try:
            q_els = page.query_selector_all(SELECTORS['popup_questions'])
            info.has_popup_questions = len([e for e in q_els if e.is_visible()]) > 0
        except Exception:
            info.has_popup_questions = False

        # Детекция form-helper-error (авточтение Сбера, верифицировано 2026-04-06)
        try:
            err_el = page.query_selector(SELECTORS['form_error'])
            info.has_form_error = bool(err_el and err_el.is_visible())
        except Exception:
            info.has_form_error = False

        # Детекция тест-формы (employer-asking-for-test, верифицировано 2026-04-06)
        try:
            test_el = page.query_selector(SELECTORS['test_form_marker'])
            info.has_test_form = bool(test_el and test_el.is_visible())
        except Exception:
            info.has_test_form = False

        # Определение типа формы
        info.form_type = self._classify_form(info, combined_text)

        return info
    
    def _extract_visible_text(self, elements) -> str:
        """Извлекает видимый текст из списка элементов"""
        texts = []
        for element in elements:
            try:
                if element.is_visible():
                    text = element.inner_text().strip()
                    if text:
                        texts.append(text)
            except:
                continue
        return " ".join(texts)
    
    def _extract_placeholders(self, inputs) -> str:
        """Извлекает placeholder'ы из полей ввода"""
        placeholders = []
        for inp in inputs:
            try:
                if inp.is_visible():
                    placeholder = inp.get_attribute('placeholder') or ""
                    if placeholder:
                        placeholders.append(placeholder)
            except:
                continue
        return " ".join(placeholders)
    
    def _analyze_progress(self, page) -> dict:
        """Анализирует наличие прогресс-индикаторов"""
        try:
            progress_elements = page.query_selector_all(SELECTORS['progress_indicators'])
            
            for element in progress_elements:
                try:
                    text = element.inner_text().lower()
                    if any(indicator in text for indicator in ['страниц', 'page', 'из', '/', 'шаг', 'step']):
                        # Пытаемся извлечь номер шага
                        import re
                        match = re.search(r'(\d+)\s*[/\s]?\s*(?:из|of|из\s+)?(\d+)?', text, re.IGNORECASE)
                        step = int(match.group(1)) if match else None
                        
                        return {'has_progress': True, 'step': step}
                except:
                    continue
            
            return {'has_progress': False, 'step': None}
            
        except:
            return {'has_progress': False, 'step': None}
    
    def _has_keywords(self, text: str, keywords: list) -> bool:
        """Проверяет наличие ключевых слов в тексте"""
        return any(keyword in text for keyword in keywords)
    
    def _classify_form(self, info: FormInfo, combined_text: str) -> FormType:
        """Классифицирует тип формы по собранной информации"""

        # 0а. Тест от работодателя — первым
        if info.has_test_form:
            return FormType.TEST_FORM

        # 0в. Попап с вопросами работодателя (vacancy-response-question в попапе)
        if info.has_popup_questions:
            return FormType.EMPLOYER_QUESTIONS

        # 0б. Авточтение (Сбер и др.): ошибка УЖЕ видна ДО отправки + кнопка чата
        #     Верифицировано 2026-04-06: form-helper-error + vacancy-response-link-view-topic в 02_snapshot
        if info.has_form_error and info.has_chat_link:
            return FormType.CHAT_INTERFACE

        # 1. Проверка на HH модалку
        if (info.has_progress or 
            self._has_keywords(combined_text, FORM_KEYWORDS['hh_modal']) or
            self._has_keywords(combined_text, FORM_KEYWORDS['navigation'])):
            
            if info.progress_step and info.progress_step > 1:
                return FormType.HH_MODAL_STEP2
            return FormType.HH_MODAL_STEP1
        
        # 2. Проверка на поле зарплаты
        if info.has_salary_field:
            return FormType.SALARY_FORM

        # 3. Проверка на чат — только через верифицированный data-qa,
        #    НЕ через текст кнопок (плавающий виджет "Чаты" даёт ложные срабатывания)
        if info.has_chat_link:
            return FormType.CHAT_INTERFACE

        # 4. Проверка на анкету работодателя
        if (info.input_count > 1 or
                self._has_keywords(combined_text, FORM_KEYWORDS['questions'])):
            return FormType.EMPLOYER_QUESTIONS
        
        # 5. Проверка на простое сопроводительное
        if (info.input_count == 1 and 
            (info.has_cover_field or self._has_keywords(combined_text, FORM_KEYWORDS['cover']))):
            return FormType.COVER_ONLY
        
        # 6. Fallback для одного поля
        if info.input_count == 1:
            return FormType.COVER_ONLY
        
        return FormType.UNKNOWN