from .base import BaseHandler, FormType, ProcessResult

class SalaryHandler(BaseHandler):
    """Handler for salary expectation forms — always skips."""

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.SALARY_FORM

    def verify_submission(self, page) -> bool:
        return True  # never called — process() always returns success=False

    def process(self, page, cover_letter: str, **kwargs) -> ProcessResult:
        """Always skips salary expectation forms."""
        return ProcessResult(
            success=False,
            status="skipped_salary_form",
            reason="Form contains a salary expectations field",
            scenario="skip_salary"
        )