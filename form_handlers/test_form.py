from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS


class TestFormHandler(BaseHandler):
    """
    Обработчик формы с тестовым заданием работодателя.

    Сценарий:
      Работодатель добавил тест/вопросы к отклику.
      Стратегия: кликаем 'Откликнуться без ответа на вопросы'
      (vacancy-response-link-no-questions), после чего открывается
      стандартная форма с cover letter и кнопкой submit.
    """

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.TEST_FORM

    def process(self, page, cover_letter: str, hr_matcher=None) -> ProcessResult:
        # 1. Кликаем "Откликнуться без ответа на вопросы"
        try:
            no_q_link = page.query_selector(SELECTORS['test_no_questions'])
            if not no_q_link or not no_q_link.is_visible():
                return ProcessResult(
                    success=False,
                    status="skipped_test_form",
                    reason="Нет ссылки пропустить вопросы — тест обязателен",
                    scenario="test_form_required"
                )

            print("   🔹 Кликаю 'Откликнуться без ответа на вопросы'...")
            no_q_link.click()
            self._wait_and_random_delay(page, 2000, 3000)
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_test_form",
                reason=f"Ошибка клика no-questions: {e}",
                scenario="test_form_error"
            )

        # 2. После клика может появиться тогл cover letter — раскрываем
        try:
            toggle = page.query_selector(SELECTORS['letter_toggle'])
            if toggle and toggle.is_visible():
                print("   🔹 Раскрываю поле сопроводительного (toggle)...")
                toggle.click()
                self._wait_and_random_delay(page, 1000, 2000)
        except Exception:
            pass

        # 3. Заполняем cover letter
        textarea = self._find_element_by_selectors(page, [
            f'[data-qa="vacancy-response-popup-form-letter-input"] textarea',
            SELECTORS['popup_letter_input'],
            '[data-qa="vacancy-response-letter-informer"] textarea',
            '[data-qa="textarea-native-wrapper"] textarea',
            'textarea',
        ])

        filled = False
        if textarea:
            print("   🔹 Заполняю сопроводительное письмо...")
            try:
                textarea.type(cover_letter, delay=10)
                filled = True
                print("   ✅ Сопроводительное заполнено")
                self._wait_and_random_delay(page, 2000, 3000)
            except Exception as e:
                print(f"   ⚠️ Ошибка заполнения: {e}")

        # 4. Ждём enabled кнопку и кликаем
        for selector in [SELECTORS['popup_submit'], SELECTORS['letter_submit']]:
            try:
                page.wait_for_selector(f"{selector}:not([disabled])", timeout=5000)
            except Exception:
                pass
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                print(f"   🔹 Кликаю: '{btn.inner_text().strip()}'")
                btn.scroll_into_view_if_needed()
                btn.click()
                self._wait_and_random_delay(page, 2000, 4000)

                if filled:
                    return ProcessResult(
                        success=True,
                        status="applied",
                        reason="Тест пропущен, сопроводительное отправлено",
                        scenario="test_form_skipped_cover_sent"
                    )
                return ProcessResult(
                    success=True,
                    status="applied_no_cover",
                    reason="Тест пропущен, отклик без сопроводительного",
                    scenario="test_form_skipped_no_cover"
                )

        return ProcessResult(
            success=False,
            status="skipped_test_form",
            reason="Тест пропущен, но кнопка submit не найдена",
            scenario="test_form_no_submit"
        )
