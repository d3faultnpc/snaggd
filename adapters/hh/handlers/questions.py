from pathlib import Path

from ..dom import find_visible
from .base import (BaseHandler, FormType, ProcessResult, choose_checkbox_options,
                   coerce_answers, is_free_text_option, norm_option, record_answers)
from config import CONFIG, SELECTORS
from utils.navigation import jam


# One normaliser for both handlers now — this was a local copy here and a bare
# .strip().lower() in hh_modal.py, and the two had already drifted apart.
_norm = norm_option


class QuestionsHandler(BaseHandler):
    """Fills employer question forms: collect all fields → one LLM batch call → fill."""

    def __init__(self, data_dir: Path):
        from core.llm_agent import LLMAgent
        try:
            self._agent = LLMAgent(data_dir=data_dir)
        except Exception as _e:
            self._agent = None
            print(f"   ⚠️ QuestionsHandler: LLMAgent not initialized: {_e}")

    # _narrate() lives on BaseHandler now (session 58 code-review — was
    # duplicated near-identically across 4 handlers, hoisted to avoid drift).

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.EMPLOYER_QUESTIONS

    def process(self, page, vacancy_text: str = "", **kwargs) -> ProcessResult:
        reporter = kwargs.get("reporter")
        _vac_seq = kwargs.get("vacancy_seq")
        vid = str(_vac_seq) if _vac_seq is not None else None
        inputs = page.query_selector_all('input[type="text"], input[type="radio"], input[type="checkbox"], textarea')
        if not inputs:
            return ProcessResult(
                success=False, status="skipped_no_inputs",
                reason="No input fields found", scenario="questions_error",
                is_terminal=True, goal_reached=False
            )

        # ── Step 1: collect text fields, radio groups, checkboxes ────────────
        text_fields = []     # (i, element, label)
        hh_letter_idx = None # index of hh's own letter field, if the page has one
        cover_letter = ""    # what was typed into it, once it has been
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
                            # hh names a group's free-text box `<input name>_text`
                            # for checkboxes exactly as it does for radios — the
                            # radio path has always addressed it that way, the
                            # checkbox path used to scan the block for "the first
                            # visible textarea" instead. Same element, weaker
                            # address; the scan stays as the fallback.
                            "name": inp.get_attribute("name") or "",
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
                    # hh's own letter field, when hh renders it inside the
                    # questionnaire page. Known by address, never by label: the
                    # label is "Сопроводительное письмо" today and hh's to
                    # change, and an employer's own question with the same
                    # wording is the case this must NOT match.
                    try:
                        if inp.evaluate(
                                "(el, sel) => !!el.closest(sel)", SELECTORS['popup_letter_input']):
                            hh_letter_idx = i
                    except Exception:
                        pass

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

        # ── Step 3: LLM batch call for all fields — including any that ask for
        # a cover/motivation letter. No keyword pre-injection: fill_form()'s
        # own prompt recognizes that by meaning and writes real content for
        # it directly, using the vacancy context it already has (session 56).
        answers: dict[str, str] = {}
        if fields and self._agent is not None:
            try:
                answers = coerce_answers(self._agent.fill_form(vacancy_text, fields))
                record_answers(kwargs.get("session_dir"), "employer_questions", fields, answers)
            except Exception as e:
                print(f"   ⚠️ LLM fill_form error: {e}")
        elif fields and self._agent is None:
            print(f"   ⚠️ LLM unavailable — {len(fields)} field(s) left blank")

        # ── Step 5: fill text / textarea fields ───────────────────────────────
        filled_count = 0
        total = len(text_fields) + len(radio_groups) + len(checkbox_groups)
        # Cases where the LLM gave an answer but it couldn't actually be applied
        # (no matching option, element interaction threw) — ambiguous, not a
        # clean "no answer" skip. Surfaced via needs_debug_review below so these
        # are retryable/inspectable instead of silently disappearing into prints.
        ambiguous_reasons: list[str] = []
        # gui_message deliberately frames this as genuine interpretation, not
        # mechanical filling — every employer's questions are freeform and
        # unknown in advance, so the model really is reasoning fresh each
        # time (llm_agent.py's own call_type tagging covers the matching
        # narration for the fill_form() call above).
        self._narrate(reporter, f"   🔹 Filling questionnaire ({len(fields)} questions)...",
                      gui_message="Reading the employer's questions…", vacancy_id=vid)

        for i, inp, label in text_fields:
            answer = answers.get(str(i), "")
            if not answer:
                print(f"   ⏭ Field {i+1}: no answer — {label[:50]}")
                continue
            try:
                inp.type(answer, delay=10)
                filled_count += 1
                if i == hh_letter_idx:
                    cover_letter = answer
                print(f"   ✅ Field {i+1}: {label[:50]}")
                page.wait_for_timeout(800)
            except Exception as e:
                print(f"   ⚠️ Field {i+1} error: {e}")
                ambiguous_reasons.append(f"text_field_error[{label[:30]}]: {e}")

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
            match_found = False
            for idx, el, val, opt_text in grp["elements"]:
                is_open = val == "open"
                matches_open = is_open and free_text is not None
                matches_text = not is_open and _norm(opt_text) == target

                if matches_open or matches_text:
                    match_found = True
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
                        ambiguous_reasons.append(f"radio_click_error[{name}]: {e}")
                    break

            if not clicked and not match_found:
                # The Каргономика failure, 2026-09-09: the model answered a radio
                # group with the literal words of the free-text option instead of
                # the `open:` form, and the only element carrying those words is
                # excluded from comparison by design. Score 88, application lost.
                jam("option_match", f"radio group offered {len(grp['options'])} option(s), "
                                    "the answer matched none of them", scope=page)
                print(f"   ⚠️ Radio '{name}': no match for '{answer[:60]}'")
                ambiguous_reasons.append(f"radio_no_match[{name}]: '{answer[:60]}'")

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
                        ambiguous_reasons.append(f"checkbox_error[{question[:30]}]: {e}")
                else:
                    print(f"   ⏭ Checkbox '{question[:50]}': unchecked ({answer or 'no answer'})")
            else:
                # A multi-select group. It was read as mutually exclusive until
                # 2026-09-09 — "pick exactly one option", compared as one whole
                # string — which is not what a checkbox is. On a live form that
                # cost the whole application: the model answered a "выберите не
                # более 3-х" question with three options, nothing matched,
                # nothing was ticked, and hh rejected a form whose other twelve
                # answers were already written. Measured over three runs against
                # the same page: two of three ended with nothing ticked here.
                answer = answers.get(grp["idx"], "")
                opts = [opt for _, _, opt in elems]
                chosen, free_text = choose_checkbox_options(answer, opts)
                ticked = 0

                for pos in chosen:
                    i, inp, opt_text = elems[pos]
                    try:
                        inp.check()
                        page.wait_for_timeout(400)
                        # Read the box back instead of trusting the click. This
                        # is also how the "не более N вариантов" cap is honoured
                        # without parsing N out of Russian prose: hh stops
                        # accepting ticks past its own limit, and a tick that
                        # did not take says so.
                        if not inp.is_checked():
                            print(f"   ⚠️ Checkbox group '{question[:50]}': "
                                  f"'{opt_text[:40]}' did not take — stopping (limit reached?)")
                            ambiguous_reasons.append(
                                f"checkbox_group_tick_refused[{question[:30]}]: "
                                f"{ticked}/{len(chosen)} applied")
                            break
                        ticked += 1
                        print(f"   ✅ Checkbox group '{question[:50]}': {opt_text}")
                    except Exception as e:
                        print(f"   ⚠️ Checkbox group click error: {e}")
                        ambiguous_reasons.append(f"checkbox_group_click_error[{question[:30]}]: {e}")
                        break

                if ticked == 0 and free_text:
                    # Genuinely none of the options fit. Only reachable when the
                    # answer named no option at all — a list of real option names
                    # is ticked above, never recited into the comment box, which
                    # is what used to happen and what nothing flagged.
                    free_el = next(((i, inp, opt) for i, inp, opt in elems
                                    if is_free_text_option(opt)), None)
                    if free_el is None:
                        jam("option_match", f"checkbox group offered {len(elems)} option(s), "
                                            "none matched and none is free text", scope=page)
                        print(f"   ⚠️ Checkbox group '{question[:50]}': no option matched "
                              f"and no free-text option exists")
                        ambiguous_reasons.append(
                            f"checkbox_group_no_match[{question[:30]}]: '{str(answer)[:60]}'")
                    else:
                        i, inp, opt_text = free_el
                        try:
                            inp.check()
                            page.wait_for_timeout(600)
                            ta = page.query_selector(f'textarea[name="{grp["name"]}_text"]') \
                                if grp.get("name") else None
                            if ta is None or not ta.is_visible():
                                ta = self._visible_textarea_in_group(inp)
                            if ta is not None and ta.is_visible():
                                ta.type(free_text, delay=10)
                                ticked += 1
                                print(f"   ✅ Checkbox group '{question[:50]}': {opt_text} + text")
                            else:
                                print(f"   ⚠️ Checkbox group '{question[:50]}': "
                                      f"{opt_text} ticked, textarea not found")
                                ambiguous_reasons.append(
                                    f"checkbox_group_open_no_textarea[{question[:30]}]")
                        except Exception as e:
                            print(f"   ⚠️ Checkbox group click error: {e}")
                            ambiguous_reasons.append(
                                f"checkbox_group_click_error[{question[:30]}]: {e}")

                if ticked:
                    filled_count += 1
                elif not str(answer).strip():
                    # No answer for a group is not the silent `continue` it used
                    # to be: if the group turns out to be required, this is the
                    # thing that rejects the form, and it should be readable
                    # before the submit rather than inferred after it.
                    print(f"   ⏭ Checkbox group '{question[:50]}': no answer")
                    ambiguous_reasons.append(f"checkbox_group_no_answer[{question[:30]}]")
                elif not any(r.startswith(("checkbox_group_no_match[",
                                           "checkbox_group_click_error[",
                                           "checkbox_group_open_no_textarea[",
                                           "checkbox_group_tick_refused["))
                             and question[:30] in r for r in ambiguous_reasons):
                    ambiguous_reasons.append(
                        f"checkbox_group_no_match[{question[:30]}]: '{str(answer)[:60]}'")

        self._narrate(reporter, f"   ✅ Filled {filled_count}/{total} questions",
                      gui_message=f"[OK] answered {filled_count}/{total} questions", vacancy_id=vid)
        self._wait_and_random_delay(page, 2000, 4000)
        result = self._submit(page, filled_count, total, cover_letter=cover_letter)
        # A cover-shaped ANSWER is still not a delivery (session 56): an
        # employer's own free-text question filled with a pitch goes through
        # the generic fill_form() path, not hh's cover mechanism, and never
        # tells a later chatik layer "goal already reached". What IS a delivery
        # is hh's own letter field rendered on this page — recognised by
        # address in the collection loop above, claimed by _submit only when
        # the form actually went out, carried across layers by adapter.py's
        # cover_delivery like the modal's own step is.
        if ambiguous_reasons:
            return self._flag_for_debug_review(
                result, "; ".join(ambiguous_reasons), ambiguous_count=len(ambiguous_reasons)
            )
        return result

    def verify_submission(self, page) -> bool:
        ok = self._poll_for_success(page, timeout_s=5)
        if not ok:
            # Not the same as "it failed": the page never showed a marker this
            # code knows. The caller already downgrades to applied_unverified —
            # 31 records on the live profile carried that status on 2026-09-09
            # (41 in the older flat log), and none of them says which of the
            # two it was.
            jam("submission_verified", "no success marker appeared within 5s", scope=page)
        return ok

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
        except Exception as e:
            # An empty label does not just mean "unlabelled": the caller drops
            # any field whose label is empty (`if label:`), so an extraction
            # crash makes the field vanish from the LLM batch entirely, never
            # gets answered, and — if it was required — resurfaces after submit
            # as "a required field failed validation", blaming the model for an
            # answer it was never asked to give.
            print(f"   ⚠️ Couldn't read a field's label ({e}) — the field will be skipped")
            return ""

    def _visible_textarea_in_group(self, inp):
        """The group's free-text box, found by walking its block.

        Fallback for when `<name>_text` does not resolve — a group whose input
        carries no name attribute, or a markup shape we have not seen.
        """
        try:
            handle = inp.evaluate_handle("""el => {
                const body = el.closest('[data-qa="task-body"]');
                if (!body) return null;
                for (const ta of body.querySelectorAll('textarea')) {
                    if (ta.offsetParent !== null) return ta;
                }
                return null;
            }""")
            return handle.as_element()
        except Exception:
            return None

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

    def _submit(self, page, filled_count: int, total: int,
                cover_letter: str = "") -> ProcessResult:
        # One finder for both tiers — see BaseHandler._find_action_button. The
        # wording tier used to scan every button on the page with no notion of
        # which form it belonged to; it is scoped to the open dialog now and
        # cannot pick up a button from one of hh's own profile surveys.
        # Address order corrected too: popup_submit is the one that appears in
        # captures, letter_submit has matched nothing since at least 2026-08.
        btn, how = self._find_action_button(
            page,
            addresses=[SELECTORS["popup_submit"], SELECTORS["letter_submit"]],
            keywords=["отправить", "откликнуться", "далее", "подтвердить"],
        )
        if btn is not None:
            try:
                label = btn.inner_text().strip()
                btn.scroll_into_view_if_needed()
                btn.click()
                self._wait_and_random_delay(page, 1000, 1500)
                # aria-invalid="true" covers standard HTML; Magritte uses a CSS class.
                # Checked on every path now — the wording fallback used to skip
                # it entirely and report a clean "applied" over a form that had
                # just rejected the submit.
                invalid = find_visible(page,
                    '[aria-invalid="true"], span[data-qa="checkbox"][class*="magritte-invalid"]')
                if invalid is not None:
                    return ProcessResult(
                        success=False, status="skipped_form_validation_error",
                        reason="Form has a required field that failed validation after submit",
                        scenario="questions_validation_error",
                        details={"filled_count": filled_count},
                        is_terminal=True, goal_reached=False
                    )
                self._wait_and_random_delay(page, 1000, 1500)
                # The letter, if hh's own field took one, goes out with this
                # submit — so the claim is made here and only here, never on a
                # form that was rejected or never sent. See COVER_ROUTES.
                delivery = ({'cover_delivered': 'questionnaire',
                             'cover_length': len(cover_letter),
                             'cover_text': cover_letter} if cover_letter else {})
                return ProcessResult(
                    success=True, status="applied",
                    reason=f"Questionnaire submitted ({filled_count} questions), "
                           f"button: '{label}' (found by {how})",
                    scenario="questions_submitted" if how == "address" else "questions_submitted_fallback",
                    details={"filled_count": filled_count, "total_fields": total,
                             "button_found_by": how, **delivery},
                    is_terminal=False, goal_reached=True
                )
            except Exception as e:
                print(f"   ⚠️ Submit click failed ({e}) — reporting as no-submit")

        jam("submit_button", f"{filled_count} answer(s) filled and nothing to submit them with",
            scope=page)
        return ProcessResult(
            success=False, status="skipped_no_submit",
            reason=f"Filled {filled_count} questions, submit button not found",
            scenario="questions_no_submit",
            details={"filled_count": filled_count, "total_fields": total},
            is_terminal=True, goal_reached=False
        )
