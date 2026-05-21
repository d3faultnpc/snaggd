from .base import BaseHandler, FormType, ProcessResult
from config import CONFIG, SELECTORS

try:
    from core.llm_agent import LLMAgent
    _agent = LLMAgent()
except Exception:
    _agent = None


class QuestionsHandler(BaseHandler):
    """Fills employer question forms: collect all fields → one LLM batch call → fill."""

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.EMPLOYER_QUESTIONS

    def process(self, page, cover_letter: str, hr_matcher=None,
                vacancy_text: str = "") -> ProcessResult:
        inputs = page.query_selector_all('input[type="text"], input[type="radio"], textarea')
        if not inputs:
            return ProcessResult(
                success=False, status="skipped_no_inputs",
                reason="Не найдены поля для заполнения", scenario="questions_error"
            )

        # ── Step 1: collect visible fields ───────────────────────────────────
        fields = []
        elements = []
        for i, inp in enumerate(inputs[:CONFIG.max_questions_per_form]):
            if not inp.is_visible():
                continue
            label = self._extract_label(inp)
            if not label:
                continue
            field_type = inp.get_attribute("type") or inp.evaluate("el => el.tagName.toLowerCase()")
            fields.append({"idx": i, "label": label, "type": field_type})
            elements.append((i, inp, label))

        if not fields:
            return ProcessResult(
                success=False, status="skipped_no_inputs",
                reason="Все поля невидимы или без лейблов", scenario="questions_error"
            )

        # ── Step 2: one LLM call for all fields ──────────────────────────────
        answers: dict[str, str] = {}
        if _agent is not None:
            try:
                answers = _agent.fill_form(vacancy_text, fields)
            except Exception as e:
                print(f"   ⚠️ LLM fill_form ошибка: {e}")
        elif hr_matcher is not None:
            # fallback: legacy per-question matching if LLM unavailable
            for f in fields:
                answers[str(f["idx"])] = hr_matcher.find_answer(f["label"])

        # ── Step 3: fill each field ───────────────────────────────────────────
        filled_count = 0
        print(f"   🔹 Заполняю анкету ({len(elements)} полей)...")
        for idx, inp, label in elements:
            answer = answers.get(str(idx), "")
            if not answer:
                print(f"   ⏭ Поле {idx+1}: нет ответа — {label[:50]}")
                continue
            try:
                if inp.get_attribute("type") == "radio":
                    inp.click()
                else:
                    inp.type(answer[:500], delay=10)
                filled_count += 1
                print(f"   ✅ Поле {idx+1}: {label[:50]}")
                page.wait_for_timeout(800)
            except Exception as e:
                print(f"   ⚠️ Ошибка поля {idx+1}: {e}")

        print(f"   ✅ Заполнено {filled_count}/{len(elements)} полей")
        self._wait_and_random_delay(page, 2000, 4000)
        return self._submit(page, filled_count, len(elements))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_label(self, inp) -> str:
        try:
            for xpath in ("xpath=..//label", "xpath=..//..//label", "xpath=..//..//div"):
                el = inp.query_selector(xpath)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        return text[:200]
            return (inp.get_attribute("placeholder") or "")[:200]
        except Exception:
            return ""

    def _submit(self, page, filled_count: int, total: int) -> ProcessResult:
        for selector in [SELECTORS["letter_submit"], SELECTORS["popup_submit"]]:
            try:
                page.wait_for_selector(f"{selector}:not([disabled])", timeout=5000)
                btn = page.query_selector(selector)
                if btn and btn.is_visible() and not btn.is_disabled():
                    label = btn.inner_text().strip()
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    self._wait_and_random_delay(page, 2000, 3000)
                    return ProcessResult(
                        success=True, status="applied",
                        reason=f"Анкета отправлена ({filled_count} полей), кнопка: '{label}'",
                        scenario="questions_submitted",
                        details={"filled_count": filled_count, "total_fields": total},
                    )
            except Exception:
                continue

        for btn in page.query_selector_all("button"):
            try:
                if not btn.is_visible() or btn.is_disabled():
                    continue
                if any(kw in btn.inner_text().lower() for kw in ["отправить", "откликнуться", "далее", "подтвердить"]):
                    btn.click()
                    self._wait_and_random_delay(page, 2000, 3000)
                    return ProcessResult(
                        success=True, status="applied",
                        reason=f"Анкета отправлена fallback ({filled_count} полей)",
                        scenario="questions_submitted_fallback",
                        details={"filled_count": filled_count},
                    )
            except Exception:
                continue

        return ProcessResult(
            success=False, status="skipped_no_submit",
            reason=f"Заполнено {filled_count} полей, кнопка не найдена",
            scenario="questions_no_submit",
            details={"filled_count": filled_count, "total_fields": total},
        )
