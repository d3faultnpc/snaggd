import re
from pathlib import Path

from .base import BaseHandler, FormType, ProcessResult
from config import CONFIG, SELECTORS


def _norm(s: str) -> str:
    """Normalize option text for comparison: nbsp, multi-space, space-before-punct."""
    s = s.replace(' ', ' ')
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\s+([.,!?;:])', r'\1', s)
    return s.strip().lower()


class QuestionsHandler(BaseHandler):
    """Fills employer question forms: collect all fields → one LLM batch call → fill."""

    def __init__(self, data_dir: Path = None):
        from core.llm_agent import LLMAgent
        _dir = data_dir or CONFIG.data_dir
        try:
            self._agent = LLMAgent(data_dir=_dir)
        except Exception as _e:
            self._agent = None
            print(f"   ⚠️ QuestionsHandler: LLMAgent not initialized: {_e}")

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.EMPLOYER_QUESTIONS

    def process(self, page, cover_letter: str, vacancy_text: str = "", **kwargs) -> ProcessResult:
        inputs = page.query_selector_all('input[type="text"], input[type="radio"], input[type="checkbox"], textarea')
        if not inputs:
            return ProcessResult(
                success=False, status="skipped_no_inputs",
                reason="No input fields found", scenario="questions_error",
                is_terminal=True, goal_reached=False
            )

        # ── Step 1: collect text fields, radio groups, checkboxes ────────────
        text_fields = []     # (i, element, label)
        radio_groups = {}    # name → {question, options, elements, has_free_text}
        checkbox_groups = {} # question_text → {idx, question, elements: [(i, inp, opt_text)], has_free_text}

        for i, inp in enumerate(inputs):
            if not inp.is_visible():
                continue
            itype = inp.get_attribute("type") or inp.evaluate("el => el.tagName.toLowerCase()")

            if itype == "radio":
                name = inp.get_attribute("name") or f"unnamed_{i}"
                opt_text = self._extract_radio_option_text(inp)
                val = inp.get_attribute("value") or ""
                if name not in radio_groups:
                    radio_groups[name] = {
                        "question": self._extract_label(inp),
                        "options": [],
                        "elements": [],
                        "has_free_text": False,
                    }
                radio_groups[name]["options"].append(opt_text)
                radio_groups[name]["elements"].append((i, inp, val, opt_text))
                if val == "open":
                    radio_groups[name]["has_free_text"] = True
            elif itype == "checkbox":
                question = self._extract_label(inp)
                option = self._extract_radio_option_text(inp)
                if question:
                    if question not in checkbox_groups:
                        checkbox_groups[question] = {
                            "idx": f"cbgroup_{len(checkbox_groups)}",
                            "question": question,
                            "elements": [],
                            "has_free_text": False,
                        }
                    opt_text = option or f"option_{i}"
                    checkbox_groups[question]["elements"].append((i, inp, opt_text))
                    if opt_text.lower() in ("свой вариант", "другое", "other"):
                        checkbox_groups[question]["has_free_text"] = True
            else:
                label = self._extract_label(inp)
                if label:
                    text_fields.append((i, inp, label))

        # ── Step 2: build LLM field specs ─────────────────────────────────────
        fields = []

        for i, inp, label in text_fields[:CONFIG.max_questions_per_form]:
            field_type = inp.get_attribute("type") or "textarea"
            fields.append({"idx": str(i), "label": label, "type": field_type})

        for name, grp in radio_groups.items():
            if not grp["question"]:
                continue
            spec = {
                "idx": f"radio_{name}",
                "label": grp["question"],
                "type": "radio_group",
                "options": grp["options"],
            }
            fields.append(spec)

        for question, grp in checkbox_groups.items():
            if len(grp["elements"]) == 1:
                i, inp, _ = grp["elements"][0]
                fields.append({"idx": f"checkbox_{i}", "label": question, "type": "checkbox"})
            else:
                fields.append({
                    "idx": grp["idx"],
                    "label": question,
                    "type": "checkbox_group",
                    "options": [opt for _, _, opt in grp["elements"]],
                })

        if not fields:
            return ProcessResult(
                success=False, status="skipped_no_inputs",
                reason="All fields are hidden or have no labels", scenario="questions_error",
                is_terminal=True, goal_reached=False
            )

        # ── Step 3: pre-inject cover letter for cover-letter text fields ───────
        answers: dict[str, str] = {}
        cover_field_keys: set[str] = set()
        if cover_letter:
            for f in fields:
                if f["type"] in ("radio_group", "checkbox_group"):
                    continue
                label_lower = f["label"].lower()
                if "сопроводительное" in label_lower or "cover letter" in label_lower:
                    answers[f["idx"]] = cover_letter
                    cover_field_keys.add(f["idx"])
                    print(f"   📝 Cover letter field detected: {f['label'][:50]}")

        # ── Step 4: LLM batch call for remaining fields ───────────────────────
        remaining = [f for f in fields if f["idx"] not in cover_field_keys]
        if remaining and self._agent is not None:
            try:
                llm_answers = self._agent.fill_form(vacancy_text, remaining)
                answers.update(llm_answers)
            except Exception as e:
                print(f"   ⚠️ LLM fill_form error: {e}")
        elif remaining and self._agent is None:
            print(f"   ⚠️ LLM unavailable — {len(remaining)} field(s) left blank")

        # ── Step 5: fill text / textarea fields ───────────────────────────────
        filled_count = 0
        total = len(text_fields) + len(radio_groups) + len(checkbox_groups)
        print(f"   🔹 Filling questionnaire ({len(fields)} questions)...")

        for i, inp, label in text_fields:
            answer = answers.get(str(i), "")
            if not answer:
                print(f"   ⏭ Field {i+1}: no answer — {label[:50]}")
                continue
            try:
                inp.type(answer, delay=10)
                filled_count += 1
                print(f"   ✅ Field {i+1}: {label[:50]}")
                page.wait_for_timeout(800)
            except Exception as e:
                print(f"   ⚠️ Field {i+1} error: {e}")

        # ── Step 6: fill radio groups ─────────────────────────────────────────
        for name, grp in radio_groups.items():
            group_key = f"radio_{name}"
            answer = answers.get(group_key, "").strip()
            if not answer:
                print(f"   ⏭ Radio group '{name}': no answer")
                continue

            # Detect "open: <free text>" pattern
            free_text = None
            if answer.lower().startswith("open:"):
                free_text = answer[5:].strip()
                target = "open"
            else:
                target = _norm(answer)

            clicked = False
            for idx, el, val, opt_text in grp["elements"]:
                is_open = val == "open"
                matches_open = is_open and free_text is not None
                matches_text = not is_open and _norm(opt_text) == target

                if matches_open or matches_text:
                    try:
                        el.click()
                        page.wait_for_timeout(600)
                        if matches_open and free_text:
                            # Wait for the animated textarea to become visible
                            hidden_ta = None
                            try:
                                page.wait_for_selector(
                                    f'textarea[name="{name}_text"]',
                                    state="visible", timeout=3000
                                )
                                hidden_ta = page.query_selector(f'textarea[name="{name}_text"]')
                            except Exception:
                                pass
                            if hidden_ta and hidden_ta.is_visible():
                                hidden_ta.type(free_text, delay=10)
                                print(f"   ✅ Radio '{name}': Свой вариант + text")
                            else:
                                print(f"   ⚠️ Radio '{name}': Свой вариант clicked, textarea not found")
                        else:
                            print(f"   ✅ Radio '{name}': {opt_text[:60]}")
                        filled_count += 1
                        clicked = True
                    except Exception as e:
                        print(f"   ⚠️ Radio '{name}' click error: {e}")
                    break

            if not clicked:
                print(f"   ⚠️ Radio '{name}': no match for '{answer[:60]}'")

        # ── Step 7: fill checkboxes ───────────────────────────────────────────
        for question, grp in checkbox_groups.items():
            elems = grp["elements"]
            if len(elems) == 1:
                # Single boolean checkbox
                i, inp, _ = elems[0]
                answer = answers.get(f"checkbox_{i}", "").strip().lower()
                if answer.startswith(("yes", "да")):
                    try:
                        inp.check()
                        filled_count += 1
                        print(f"   ✅ Checkbox '{question[:50]}': checked")
                        page.wait_for_timeout(400)
                    except Exception as e:
                        print(f"   ⚠️ Checkbox '{question[:50]}' error: {e}")
                else:
                    print(f"   ⏭ Checkbox '{question[:50]}': unchecked ({answer or 'no answer'})")
            else:
                # Mutually exclusive group — pick exactly one option
                answer = answers.get(grp["idx"], "").strip()
                if not answer:
                    print(f"   ⏭ Checkbox group '{question[:50]}': no answer")
                    continue
                free_text = None
                if answer.lower().startswith("open:"):
                    free_text = answer[5:].strip()
                    target = "open"
                else:
                    target = _norm(answer)
                clicked = False
                for i, inp, opt_text in elems:
                    norm_opt = _norm(opt_text)
                    is_free = norm_opt in ("свой вариант", "другое", "other")
                    matches_free = is_free and (free_text is not None or target in ("свой вариант", "другое", "other"))
                    matches_opt = not is_free and norm_opt == target
                    if matches_free or matches_opt:
                        try:
                            inp.check()
                            page.wait_for_timeout(600)
                            if is_free and free_text:
                                try:
                                    ta = inp.evaluate_handle("""el => {
                                        const body = el.closest('[data-qa="task-body"]');
                                        if (!body) return null;
                                        for (const ta of body.querySelectorAll('textarea')) {
                                            if (ta.offsetParent !== null) return ta;
                                        }
                                        return null;
                                    }""")
                                    ta_el = ta.as_element()
                                    if ta_el and ta_el.is_visible():
                                        ta_el.type(free_text, delay=10)
                                        print(f"   ✅ Checkbox group '{question[:50]}': Свой вариант + text")
                                    else:
                                        print(f"   ⚠️ Checkbox group '{question[:50]}': Свой вариант — textarea not found")
                                except Exception as e:
                                    print(f"   ⚠️ Свой вариант textarea: {e}")
                            else:
                                print(f"   ✅ Checkbox group '{question[:50]}': {opt_text}")
                            filled_count += 1
                            clicked = True
                        except Exception as e:
                            print(f"   ⚠️ Checkbox group click error: {e}")
                        break
                if not clicked:
                    print(f"   ⚠️ Checkbox group '{question[:50]}': no match for '{answer[:60]}'")

        print(f"   ✅ Filled {filled_count}/{total} questions")
        self._wait_and_random_delay(page, 2000, 4000)
        return self._submit(page, filled_count, total)

    def verify_submission(self, page) -> bool:
        return self._poll_for_success(page, timeout_s=5)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_label(self, inp) -> str:
        try:
            text = inp.evaluate("""el => {
                const body = el.closest('[data-qa="task-body"]');
                if (body) {
                    const q = body.querySelector('[data-qa="task-question"]');
                    if (q && q.innerText.trim()) return q.innerText.trim();
                }
                return '';
            }""")
            if text and text.strip():
                return text.strip()[:300]
            for xpath in ("xpath=..//label", "xpath=..//..//label"):
                el = inp.query_selector(xpath)
                if el:
                    t = el.inner_text().strip()
                    if t:
                        return t[:300]
            return (inp.get_attribute("placeholder") or "")[:200]
        except Exception:
            return ""

    def _extract_radio_option_text(self, inp) -> str:
        try:
            return inp.evaluate("""el => {
                const norm = s => s.replace(/ /g, ' ').trim();
                // Magritte: option text lives in data-qa="cell-text-content" sibling
                const cell = el.closest('[data-qa="cell"]');
                if (cell) {
                    const t = cell.querySelector('[data-qa="cell-text-content"]');
                    if (t && norm(t.innerText)) return norm(t.innerText);
                }
                const lbl = el.closest('label');
                if (lbl) return norm(lbl.innerText);
                const id = el.id;
                if (id) {
                    const forLbl = document.querySelector('label[for="' + id + '"]');
                    if (forLbl) return norm(forLbl.innerText);
                }
                const next = el.nextElementSibling;
                if (next) return norm(next.innerText);
                return el.value || '';
            }""")
        except Exception:
            return inp.get_attribute("value") or ""

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
                    try:
                        # aria-invalid="true" covers standard HTML; Magritte uses a CSS class
                        invalid = page.query_selector(
                            '[aria-invalid="true"], span[data-qa="checkbox"][class*="magritte-invalid"]'
                        )
                        if invalid and invalid.is_visible():
                            return ProcessResult(
                                success=False, status="skipped_form_validation_error",
                                reason="Form has a required field that failed validation after submit",
                                scenario="questions_validation_error",
                                details={"filled_count": filled_count},
                                is_terminal=True, goal_reached=False
                            )
                    except Exception:
                        pass
                    self._wait_and_random_delay(page, 1000, 1500)
                    return ProcessResult(
                        success=True, status="applied",
                        reason=f"Questionnaire submitted ({filled_count} questions), button: '{label}'",
                        scenario="questions_submitted",
                        details={"filled_count": filled_count, "total_fields": total},
                        is_terminal=False, goal_reached=True
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
                        reason=f"Questionnaire submitted via fallback ({filled_count} questions)",
                        scenario="questions_submitted_fallback",
                        details={"filled_count": filled_count},
                        is_terminal=False, goal_reached=True
                    )
            except Exception:
                continue

        return ProcessResult(
            success=False, status="skipped_no_submit",
            reason=f"Filled {filled_count} questions, submit button not found",
            scenario="questions_no_submit",
            details={"filled_count": filled_count, "total_fields": total},
            is_terminal=True, goal_reached=False
        )
