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

    def process(self, page, cover_letter: str, vacancy_text: str = "", **kwargs) -> ProcessResult:
        inputs = page.query_selector_all('input[type="text"], input[type="radio"], textarea')
        if not inputs:
            return ProcessResult(
                success=False, status="skipped_no_inputs",
                reason="No input fields found", scenario="questions_error"
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
                reason="All fields are hidden or have no labels", scenario="questions_error"
            )

        # ── Step 2: pre-inject cover letter for cover-letter fields ─────────────
        # The pre-generated cover_letter (from cover_letter.md prompt) must be used
        # directly for cover letter fields — never routed through fill_form which uses
        # a generic form-fill prompt and produces low-quality output.
        answers: dict[str, str] = {}
        cover_field_keys = set()
        if cover_letter:
            for f in fields:
                label_lower = f["label"].lower()
                if "сопроводительное" in label_lower or "cover letter" in label_lower:
                    answers[str(f["idx"])] = cover_letter
                    cover_field_keys.add(str(f["idx"]))
                    print(f"   📝 Cover letter field detected: {f['label'][:50]}")

        # Non-cover fields: one LLM batch call via fill_form
        remaining_fields = [f for f in fields if str(f["idx"]) not in cover_field_keys]
        if remaining_fields and _agent is not None:
            try:
                llm_answers = _agent.fill_form(vacancy_text, remaining_fields)
                answers.update(llm_answers)
            except Exception as e:
                print(f"   ⚠️ LLM fill_form error: {e}")
        elif remaining_fields and _agent is None:
            # LLM unavailable — leave fields empty, log clearly
            print(f"   ⚠️ LLM unavailable — {len(remaining_fields)} field(s) left blank")

        # ── Step 3: fill each field ───────────────────────────────────────────
        filled_count = 0
        print(f"   🔹 Filling questionnaire ({len(elements)} fields)...")
        for idx, inp, label in elements:
            answer = answers.get(str(idx), "")
            if not answer:
                print(f"   ⏭ Field {idx+1}: no answer — {label[:50]}")
                continue
            try:
                if inp.get_attribute("type") == "radio":
                    inp.click()
                else:
                    inp.type(answer[:500], delay=10)
                filled_count += 1
                print(f"   ✅ Field {idx+1}: {label[:50]}")
                page.wait_for_timeout(800)
            except Exception as e:
                print(f"   ⚠️ Field {idx+1} error: {e}")

        print(f"   ✅ Filled {filled_count}/{len(elements)} fields")
        self._wait_and_random_delay(page, 2000, 4000)
        return self._submit(page, filled_count, len(elements))

    def verify_submission(self, page) -> bool:
        return self._poll_for_success(page, timeout_s=5)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_label(self, inp) -> str:
        try:
            # task-question format: question text is in the parent block, linked via aria-labelledby
            text = inp.evaluate("""el => {
                // task-body wraps both the question text (task-question) and the textarea
                const body = el.closest('[data-qa="task-body"]');
                if (body) {
                    const q = body.querySelector('[data-qa="task-question"]');
                    if (q && q.innerText.trim()) return q.innerText.trim();
                }
                return '';
            }""")
            if text and text.strip():
                return text.strip()[:300]
            # popup/modal format: label tag nearby
            for xpath in ("xpath=..//label", "xpath=..//..//label"):
                el = inp.query_selector(xpath)
                if el:
                    t = el.inner_text().strip()
                    if t:
                        return t[:300]
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
                    self._wait_and_random_delay(page, 1000, 1500)
                    # Guard: required field validation error
                    try:
                        invalid = page.query_selector('[aria-invalid="true"]')
                        if invalid and invalid.is_visible():
                            return ProcessResult(
                                success=False, status="skipped_form_validation_error",
                                reason="Form has a required field that failed validation after submit",
                                scenario="questions_validation_error",
                                details={"filled_count": filled_count},
                            )
                    except Exception:
                        pass
                    self._wait_and_random_delay(page, 1000, 1500)
                    return ProcessResult(
                        success=True, status="applied",
                        reason=f"Questionnaire submitted ({filled_count} fields), button: '{label}'",
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
                        reason=f"Questionnaire submitted via fallback ({filled_count} fields)",
                        scenario="questions_submitted_fallback",
                        details={"filled_count": filled_count},
                    )
            except Exception:
                continue

        return ProcessResult(
            success=False, status="skipped_no_submit",
            reason=f"Filled {filled_count} fields, submit button not found",
            scenario="questions_no_submit",
            details={"filled_count": filled_count, "total_fields": total},
        )
