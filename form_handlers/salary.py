from .base import BaseHandler, FormType, ProcessResult

class SalaryHandler(BaseHandler):
    """Обработчик форм с полями зарплатных ожиданий - всегда скип"""
    
    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.SALARY_FORM
    
    def process(self, page, cover_letter: str, hr_matcher=None) -> ProcessResult:
        """Всегда скипаем формы с ЗП"""
        return ProcessResult(
            success=False,
            status="skipped_salary_form",
            reason="Форма содержит поле зарплатных ожиданий",
            scenario="skip_salary"
        )