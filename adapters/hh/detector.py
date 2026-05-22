from typing import Optional
from .handlers.base import FormType, FormInfo
from config import SELECTORS, FORM_KEYWORDS

class FormDetector:
    """DOM-based form type detector (no LLM)."""

    def detect(self, page) -> FormInfo:
        """Determines form type from current DOM state."""

        inputs = page.query_selector_all(SELECTORS['inputs_all'])
        labels = page.query_selector_all(SELECTORS['labels'])
        buttons = page.query_selector_all(SELECTORS['buttons'])

        all_labels_text = self._extract_visible_text(labels)
        all_buttons_text = self._extract_visible_text(buttons)
        all_placeholders = self._extract_placeholders(inputs)

        combined_text = f"{all_labels_text} {all_buttons_text} {all_placeholders}".lower()

        info = FormInfo(
            form_type=FormType.UNKNOWN,
            input_count=len([inp for inp in inputs if inp.is_visible()]),
            labels=[label.inner_text().strip() for label in labels if label.is_visible()],
            placeholders=all_placeholders.split(),
            buttons=[btn.inner_text().strip() for btn in buttons if btn.is_visible()]
        )
        
        progress_info = self._analyze_progress(page)
        info.has_progress = progress_info['has_progress']
        info.progress_step = progress_info['step']

        info.has_salary_field = self._has_keywords(combined_text, FORM_KEYWORDS['salary'])
        info.has_cover_field = self._has_keywords(combined_text, FORM_KEYWORDS['cover'])

        # Chat link detected via verified data-qa (2026-04-05)
        try:
            chat_el = page.query_selector('[data-qa="vacancy-response-link-view-topic"]')
            info.has_chat_link = bool(chat_el and chat_el.is_visible())
        except Exception:
            info.has_chat_link = False
        
        # Employer questions detected in popup (vacancy-response-question, verified 2026-04-06)
        try:
            q_els = page.query_selector_all(SELECTORS['popup_questions'])
            info.has_popup_questions = len([e for e in q_els if e.is_visible()]) > 0
        except Exception:
            info.has_popup_questions = False

        # Employer questions detected as full-page questionnaire (task-question, observed 2026-05-22)
        try:
            tq_els = page.query_selector_all('[data-qa="task-question"]')
            info.has_task_questions = len([e for e in tq_els if e.is_visible()]) > 0
        except Exception:
            info.has_task_questions = False

        # form-helper-error detection (Sber auto-read pattern, verified 2026-04-06)
        try:
            err_el = page.query_selector(SELECTORS['form_error'])
            info.has_form_error = bool(err_el and err_el.is_visible())
        except Exception:
            info.has_form_error = False

        # Test form detection (employer-asking-for-test, verified 2026-04-06)
        try:
            test_el = page.query_selector(SELECTORS['test_form_marker'])
            info.has_test_form = bool(test_el and test_el.is_visible())
        except Exception:
            info.has_test_form = False

        info.form_type = self._classify_form(info, combined_text)

        return info
    
    def _extract_visible_text(self, elements) -> str:
        """Extracts visible text from a list of elements."""
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
        """Extracts placeholder attributes from input elements."""
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
        """Detects progress indicators (multi-step forms)."""
        try:
            progress_elements = page.query_selector_all(SELECTORS['progress_indicators'])

            for element in progress_elements:
                try:
                    text = element.inner_text().lower()
                    if any(indicator in text for indicator in ['страниц', 'page', 'из', '/', 'шаг', 'step']):
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
        """Returns True if any keyword is found in text."""
        return any(keyword in text for keyword in keywords)
    
    def _classify_form(self, info: FormInfo, combined_text: str) -> FormType:
        """Classifies form type based on collected DOM signals."""

        # 0a. Employer test — highest priority
        if info.has_test_form:
            return FormType.TEST_FORM

        # 0c. Employer questions — popup (vacancy-response-question) or full-page (task-question)
        if info.has_popup_questions or info.has_task_questions:
            return FormType.EMPLOYER_QUESTIONS

        # 0b. Auto-read pattern (Sber etc.): error already visible BEFORE submit + chat button.
        #     Verified 2026-04-06: form-helper-error + vacancy-response-link-view-topic in 02_snapshot.
        if info.has_form_error and info.has_chat_link:
            return FormType.CHAT_INTERFACE

        # 1. HH modal (multi-step)
        if (info.has_progress or
            self._has_keywords(combined_text, FORM_KEYWORDS['hh_modal']) or
            self._has_keywords(combined_text, FORM_KEYWORDS['navigation'])):

            if info.progress_step and info.progress_step > 1:
                return FormType.HH_MODAL_STEP2
            return FormType.HH_MODAL_STEP1

        # 2. Salary field
        if info.has_salary_field:
            return FormType.SALARY_FORM

        # 3. Chat — only via verified data-qa, NOT button text
        #    (floating "Chats" widget causes false positives when matching text)
        if info.has_chat_link:
            return FormType.CHAT_INTERFACE

        # 4. Employer questionnaire
        if (info.input_count > 1 or
                self._has_keywords(combined_text, FORM_KEYWORDS['questions'])):
            return FormType.EMPLOYER_QUESTIONS

        # 5. Single cover letter field
        if (info.input_count == 1 and
            (info.has_cover_field or self._has_keywords(combined_text, FORM_KEYWORDS['cover']))):
            return FormType.COVER_ONLY

        # 6. Fallback for single unknown field
        if info.input_count == 1:
            return FormType.COVER_ONLY
        
        return FormType.UNKNOWN