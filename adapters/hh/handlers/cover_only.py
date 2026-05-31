from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS

class CoverOnlyHandler(BaseHandler):
    """Handler for forms with a single cover letter field."""

    def can_handle(self, form_type: FormType) -> bool:
        return form_type in [FormType.COVER_ONLY, FormType.UNKNOWN]

    def process(self, page, cover_letter: str, **kwargs) -> ProcessResult:
        """Fills the cover letter field and submits."""

        textarea = self._find_element_by_selectors(page, SELECTORS['cover_textarea'])

        if not textarea:
            return ProcessResult(
                success=False,
                status="skipped_no_textarea",
                reason="Cover letter field not found",
                scenario="cover_error",
                is_terminal=True,
                goal_reached=False
            )

        # Guard against misdetected salary fields
        if self._is_salary_field(textarea):
            return ProcessResult(
                success=False,
                status="skipped_salary_form",
                reason="Salary expectations field detected instead of cover letter",
                scenario="salary_detection",
                is_terminal=True,
                goal_reached=False
            )

        try:
            print("   🔹 Filling cover letter...")
            textarea.type(cover_letter, delay=10)
            print("   ✅ Cover letter filled")

            self._wait_and_random_delay(page, 2000, 3000)

            send_button = self._find_element_by_selectors(page, SELECTORS['send_button'])

            if not send_button:
                return ProcessResult(
                    success=False,
                    status="skipped_no_send_button",
                    reason="Submit button not found",
                    scenario="cover_error",
                    is_terminal=True,
                    goal_reached=False
                )

            print("   🔹 Submitting application...")
            send_button.click()

            self._wait_and_random_delay(page, 3000, 5000)
            print("   ✅ Application submitted!")

            return ProcessResult(
                success=True,
                status="applied",
                reason="Cover letter submitted",
                scenario="cover_only",
                details={'cover_length': len(cover_letter)},
                is_terminal=True,
                goal_reached=True
            )

        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_error",
                reason=f"Form fill error: {str(e)}",
                scenario="cover_error",
                is_terminal=True,
                goal_reached=False
            )
    
    def verify_submission(self, page) -> bool:
        return self._poll_for_success(page, timeout_s=5)

    def _is_salary_field(self, element) -> bool:
        """Returns True if the element is a salary expectations field."""
        try:
            placeholder = element.get_attribute('placeholder') or ""
            label = element.query_selector('xpath=..//label') or element.query_selector('xpath=..//..//label')
            label_text = label.inner_text().strip().lower() if label else ""
            salary_keywords = ['зарплат', 'salary', 'ожидани', 'expected salary', 'доход', 'желаем']
            return any(kw in placeholder.lower() or kw in label_text for kw in salary_keywords)
        except:
            return False