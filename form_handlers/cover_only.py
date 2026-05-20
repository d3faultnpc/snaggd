from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS

class CoverOnlyHandler(BaseHandler):
    """Обработчик формы с единственным полем сопроводительного письма"""
    
    def can_handle(self, form_type: FormType) -> bool:
        return form_type in [FormType.COVER_ONLY, FormType.UNKNOWN]
    
    def process(self, page, cover_letter: str, hr_matcher=None) -> ProcessResult:
        """Заполняет сопроводительное письмо и отправляет"""
        
        # Ищем textarea для сопроводительного
        textarea = self._find_element_by_selectors(page, SELECTORS['cover_textarea'])
        
        if not textarea:
            return ProcessResult(
                success=False,
                status="skipped_no_textarea",
                reason="Не найдено поле для сопроводительного письма",
                scenario="cover_error"
            )
        
        # Проверяем, не является ли это полем ЗП
        if self._is_salary_field(textarea):
            return ProcessResult(
                success=False,
                status="skipped_salary_form",
                reason="Обнаружено поле зарплатных ожиданий вместо сопроводительного",
                scenario="salary_detection"
            )
        
        try:
            # Заполняем сопроводительное
            print("   🔹 Заполняю сопроводительное письмо...")
            textarea.fill(cover_letter)
            print("   ✅ Сопроводительное письмо заполнено")
            
            # Ждём немного
            self._wait_and_random_delay(page, 2000, 3000)
            
            # Ищем кнопку отправки
            send_button = self._find_element_by_selectors(page, SELECTORS['send_button'])
            
            if not send_button:
                return ProcessResult(
                    success=False,
                    status="skipped_no_send_button",
                    reason="Не найдена кнопка отправки",
                    scenario="cover_error"
                )
            
            # Отправляем
            print("   🔹 Отправляю отклик...")
            send_button.click()
            
            # Ждём отправки
            self._wait_and_random_delay(page, 3000, 5000)
            print("   ✅ Отклик отправлен!")
            
            return ProcessResult(
                success=True,
                status="applied",
                reason="Сопроводительное письмо отправлено",
                scenario="cover_only",
                details={'cover_length': len(cover_letter)}
            )
            
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_error",
                reason=f"Ошибка при заполнении формы: {str(e)}",
                scenario="cover_error"
            )
    
    def _is_salary_field(self, element) -> bool:
        """Проверяет, является ли поле полем для зарплаты"""
        try:
            # Проверяем placeholder
            placeholder = element.get_attribute('placeholder') or ""
            
            # Проверяем label
            label = element.query_selector('xpath=..//label') or element.query_selector('xpath=..//..//label')
            label_text = label.inner_text().strip().lower() if label else ""
            
            # Keywords для определения полей ЗП
            salary_keywords = ['зарплат', 'salary', 'ожидани', 'expected salary', 'доход', 'желаем']
            
            return any(kw in placeholder.lower() or kw in label_text for kw in salary_keywords)
            
        except:
            return False