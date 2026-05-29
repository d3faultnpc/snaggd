from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS, CONFIG

try:
    from core.llm_agent import LLMAgent
    _agent = LLMAgent()
except Exception:
    _agent = None


class TestFormHandler(BaseHandler):
    """
    Handler for employer test/question forms.

    Strategy: click 'Apply without answering questions'
    (vacancy-response-link-no-questions), which opens the standard
    form with a cover letter field and submit button.
    """

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.TEST_FORM

    def verify_submission(self, page) -> bool:
        return self._poll_for_success(page, timeout_s=5)

    def process(self, page, cover_letter: str, **kwargs) -> ProcessResult:
        # 1. Click "Apply without answering questions"
        try:
            no_q_link = page.query_selector(SELECTORS['test_no_questions'])
            if not no_q_link or not no_q_link.is_visible():
                if CONFIG.fill_tests:
                    # FILL_TESTS=true: delegate to LLM fill — not yet implemented
                    return ProcessResult(
                        success=False,
                        status="skipped_test_form",
                        reason="Test mandatory — LLM fill not yet implemented (FILL_TESTS=true noted)",
                        scenario="test_form_fill_pending"
                    )
                return ProcessResult(
                    success=False,
                    status="skipped_test_form",
                    reason="No skip-questions link found — test is mandatory",
                    scenario="test_form_required"
                )

            print("   🔹 Clicking 'Apply without answering questions'...")
            no_q_link.click()
            self._wait_and_random_delay(page, 2000, 3000)
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_test_form",
                reason=f"Error clicking no-questions link: {e}",
                scenario="test_form_error"
            )

        # 2. A cover letter toggle may appear after the click — expand it
        try:
            toggle = page.query_selector(SELECTORS['letter_toggle'])
            if toggle and toggle.is_visible():
                print("   🔹 Expanding cover letter field (toggle)...")
                toggle.click()
                self._wait_and_random_delay(page, 1000, 2000)
        except Exception:
            pass

        # 2.5. Fill remaining employer question fields (salary, custom questions)
        # These are task-body textareas that stay after the "без теста" click.
        self._fill_employer_questions(page, kwargs.get('vacancy_text', ''))

        # 3. Fill cover letter — use specific selectors first, then fallback that
        # explicitly excludes task-body (employer question) textareas filled in step 2.5
        textarea = self._find_element_by_selectors(page, [
            '[data-qa="vacancy-response-popup-form-letter-input"] textarea',
            SELECTORS['popup_letter_input'],
            '[data-qa="vacancy-response-letter-informer"] textarea',
        ])
        if not textarea:
            for t in page.query_selector_all('textarea'):
                if t.is_visible():
                    in_task_body = t.evaluate("el => !!el.closest('[data-qa=\"task-body\"]')")
                    if not in_task_body:
                        textarea = t
                        break

        filled = False
        if textarea:
            print("   🔹 Filling cover letter...")
            try:
                textarea.type(cover_letter, delay=10)
                filled = True
                print("   ✅ Cover letter filled")
                self._wait_and_random_delay(page, 2000, 3000)
            except Exception as e:
                print(f"   ⚠️ Fill error: {e}")

        # 4. Wait for the submit button to become enabled, then click
        for selector in [SELECTORS['popup_submit'], SELECTORS['letter_submit']]:
            try:
                page.wait_for_selector(f"{selector}:not([disabled])", timeout=5000)
            except Exception:
                pass
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                print(f"   🔹 Clicking: '{btn.inner_text().strip()}'")
                btn.scroll_into_view_if_needed()
                btn.click()
                self._wait_and_random_delay(page, 1500, 2500)

                # Guard: check for validation errors (required field left empty)
                try:
                    invalid = page.query_selector('[aria-invalid="true"]')
                    if invalid and invalid.is_visible():
                        return ProcessResult(
                            success=False,
                            status="skipped_form_validation_error",
                            reason="Form has a required field that failed validation after submit",
                            scenario="test_form_validation_error"
                        )
                except Exception:
                    pass

                self._wait_and_random_delay(page, 500, 1500)
                if filled:
                    return ProcessResult(
                        success=True,
                        status="applied",
                        reason="Test skipped, cover letter submitted",
                        scenario="test_form_skipped_cover_sent"
                    )
                return ProcessResult(
                    success=True,
                    status="applied_no_cover",
                    reason="Test skipped, application submitted without cover letter",
                    scenario="test_form_skipped_no_cover"
                )

        return ProcessResult(
            success=False,
            status="skipped_test_form",
            reason="Test skipped but submit button not found",
            scenario="test_form_no_submit"
        )

    def _fill_employer_questions(self, page, vacancy_text: str) -> None:
        """Fill task-body employer questions (salary, custom) that remain after 'без теста' click."""
        if _agent is None:
            return
        try:
            task_inputs = page.query_selector_all('[data-qa="task-body"] textarea, [data-qa="task-body"] input[type="text"]')
            if not task_inputs:
                return
            fields = []
            fillable = []
            for i, el in enumerate(task_inputs):
                if not el.is_visible():
                    continue
                label = el.evaluate("""el => {
                    const body = el.closest('[data-qa="task-body"]');
                    if (body) {
                        const q = body.querySelector('[data-qa="task-question"]');
                        if (q && q.innerText.trim()) return q.innerText.trim();
                    }
                    return el.getAttribute('placeholder') || '';
                }""")
                if label:
                    fields.append({"idx": i, "label": label, "type": "textarea"})
                    fillable.append((i, el))
            if not fields:
                return
            print(f"   🔹 Filling {len(fields)} employer question(s) (task-body)...")
            answers = _agent.fill_form(vacancy_text, fields)
            for idx, el in fillable:
                answer = answers.get(str(idx), "")
                if answer:
                    el.type(answer[:500], delay=10)
                    self._wait_and_random_delay(page, 400, 800)
                    print(f"   ✅ Employer question answered: {answer[:60]}")
        except Exception as e:
            print(f"   ⚠️ Employer question fill error: {e}")
