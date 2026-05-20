from .base import BaseHandler, FormType, ProcessResult
from config import CONFIG, SELECTORS

class QuestionsHandler(BaseHandler):
    """Обработчик анкет работодателя с несколькими вопросами"""
    
    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.EMPLOYER_QUESTIONS
    
    def process(self, page, cover_letter: str, hr_matcher=None) -> ProcessResult:
        """Заполняет анкету работодателя"""
        
        if not hr_matcher:
            return ProcessResult(
                success=False,
                status="skipped_no_hr_matcher",
                reason="HR matcher не инициализирован",
                scenario="questions_error"
            )
        
        # Ищем все поля ввода
        inputs = page.query_selector_all('input[type="text"], input[type="radio"], textarea')
        
        if not inputs:
            return ProcessResult(
                success=False,
                status="skipped_no_inputs",
                reason="Не найдены поля для заполнения",
                scenario="questions_error"
            )
        
        filled_count = 0
        max_questions = CONFIG.max_questions_per_form
        
        try:
            print(f"   🔹 Заполняю анкету ({len(inputs[:max_questions])} полей)...")
            
            for i, inp in enumerate(inputs[:max_questions]):
                try:
                    if not inp.is_visible():
                        continue
                    
                    # Ищем вопрос/лейбл для поля
                    question_text = self._extract_question_text(inp)
                    
                    if not question_text:
                        print(f"   ⏭ Пропуск поля {i+1}: не найден текст вопроса")
                        continue
                    
                    # Проверяем, не является ли это полем ЗП
                    if self._is_salary_question(question_text):
                        print(f"   ⏭ Пропуск поля {i+1}: вопрос о зарплате")
                        continue
                    
                    # Получаем ответ через HR matcher
                    answer = hr_matcher.find_answer(question_text)
                    
                    # Заполняем поле
                    if inp.get_attribute('type') == 'radio':
                        inp.click()
                    else:
                        # Ограничиваем длину ответа
                        answer = answer[:500]
                        # type() триггерит React события per-keystroke (нужно для enabled кнопки)
                        inp.type(answer, delay=10)
                    
                    filled_count += 1
                    print(f"   ✅ Поле {i+1}: {question_text[:50]}...")
                    
                    # Небольшая пауза между полями
                    page.wait_for_timeout(1000)
                    
                except Exception as e:
                    print(f"   ⚠️ Ошибка заполнения поля {i+1}: {e}")
                    continue
            
            print(f"   ✅ Заполнено {filled_count} полей анкеты")

            # Человеческая пауза после заполнения
            self._wait_and_random_delay(page, 2000, 4000)

            # Нажимаем submit
            submit_result = self._submit(page, filled_count, len(inputs))
            return submit_result
            
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_error",
                reason=f"Ошибка при заполнении анкеты: {str(e)}",
                scenario="questions_error"
            )
    
    def _submit(self, page, filled_count: int, total_fields: int) -> ProcessResult:
        """Ищет и нажимает кнопку отправки формы."""
        for selector in [SELECTORS['letter_submit'], SELECTORS['popup_submit']]:
            try:
                try:
                    page.wait_for_selector(f"{selector}:not([disabled])", timeout=5000)
                except Exception:
                    pass
                btn = page.query_selector(selector)
                if btn and btn.is_visible() and not btn.is_disabled():
                    btn_text = btn.inner_text().strip()
                    print(f"   🔹 Отправляю анкету: '{btn_text}'")
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    self._wait_and_random_delay(page, 2000, 3000)
                    return ProcessResult(
                        success=True,
                        status="applied",
                        reason=f"Анкета отправлена ({filled_count} полей), кнопка: '{btn_text}'",
                        scenario="questions_submitted",
                        details={'filled_count': filled_count, 'total_fields': total_fields}
                    )
            except Exception:
                continue

        # Fallback по тексту кнопки
        for btn in page.query_selector_all('button'):
            try:
                if not btn.is_visible() or btn.is_disabled():
                    continue
                text = btn.inner_text().strip().lower()
                if any(kw in text for kw in ['отправить', 'откликнуться', 'далее', 'подтвердить']):
                    print(f"   🔹 Отправляю анкету (fallback): '{btn.inner_text().strip()}'")
                    btn.click()
                    self._wait_and_random_delay(page, 2000, 3000)
                    return ProcessResult(
                        success=True,
                        status="applied",
                        reason=f"Анкета отправлена через fallback ({filled_count} полей)",
                        scenario="questions_submitted_fallback",
                        details={'filled_count': filled_count}
                    )
            except Exception:
                continue

        # Нет кнопки — форма заполнена но не отправлена
        return ProcessResult(
            success=False,
            status="skipped_no_submit",
            reason=f"Заполнено {filled_count} полей, кнопка отправки не найдена",
            scenario="questions_no_submit",
            details={'filled_count': filled_count, 'total_fields': total_fields}
        )

    def _extract_question_text(self, input_element) -> str:
        """Извлекает текст вопроса для поля ввода"""
        try:
            # Ищем label
            label = (input_element.query_selector('xpath=..//label') or 
                    input_element.query_selector('xpath=..//..//label') or
                    input_element.query_selector('xpath=..//..//div'))
            
            if label:
                return label.inner_text().strip()[:200]
            
            # Fallback на placeholder
            placeholder = input_element.get_attribute('placeholder') or ""
            return placeholder[:200]
            
        except:
            return ""
    
    def _is_salary_question(self, question_text: str) -> bool:
        """Проверяет, является ли вопрос вопросом о зарплате"""
        question_lower = question_text.lower()
        salary_keywords = ['зарплат', 'salary', 'ожидани', 'доход', 'заработ']
        return any(kw in question_lower for kw in salary_keywords)