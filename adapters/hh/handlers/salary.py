from .base import BaseHandler, FormType, ProcessResult

class SalaryHandler(BaseHandler):
    """Handler for salary expectation forms — always skips."""

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.SALARY_FORM

    def process(self, page, cover_letter: str, hr_matcher=None) -> ProcessResult:
        """Always skips salary expectation forms."""
        return ProcessResult(
            success=False,
            status="skipped_salary_form",
            reason="Форма содержит поле зарплатных ожиданий",
            scenario="skip_salary"
        )