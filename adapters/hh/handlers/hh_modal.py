from .base import BaseHandler, FormType, ProcessResult
from ..dom import find_chat_link, find_visible, iter_visible
from config import SELECTORS, FORM_KEYWORDS

class HHModalHandler(BaseHandler):
    """
    Handler for HH modals with navigation.

    Scenarios:
      A. Recognized cover-letter textarea (real HH data-qa markup, not a
         keyword guess) → fill on demand → "Submit" → success.
      B. No cover textarea — this step is one of HH's own variable
         screening steps instead (location, salary expectation, English
         level, relocation, whatever else gets added later): collect
         whatever's visible blindly, one LLM batch call decides content per
         field, same fill_form() mechanism questions.py uses (session 56).
      C. After "Submit": error "Application already viewed" → click "Chat".
    """

    def __init__(self, data_dir):
        # See ChatHandler.__init__ for why this is per-instance rather than a
        # module-level agent: the screening answers this handler produces were
        # grounded in the flat legacy candidate.md, not the active profile's.
        from core.llm_agent import LLMAgent
        try:
            self._agent = LLMAgent(data_dir=data_dir)
        except Exception as _e:
            self._agent = None
            print(f"   ⚠️ HHModalHandler: LLMAgent not initialized: {_e}")

    # _narrate() lives on BaseHandler now (session 58 code-review — was
    # duplicated near-identically across 4 handlers, hoisted to avoid drift).

    def can_handle(self, form_type: FormType) -> bool:
        return form_type in [FormType.HH_MODAL_STEP1, FormType.HH_MODAL_STEP2]

    def process(self, page, **kwargs) -> ProcessResult:
        llm_cover = kwargs.get("llm_cover")
        vacancy_id = kwargs.get("vacancy_id")
        vacancy_text = kwargs.get("vacancy_text", "")
        reporter = kwargs.get("reporter")
        _vac_seq = kwargs.get("vacancy_seq")
        vid = str(_vac_seq) if _vac_seq is not None else None
        ambiguous_reasons: list[str] = []

        # 1. Find the recognized cover-letter textarea (real HH selector, not
        # a keyword match on label text — legitimate structural recognition).
        textarea = self._find_cover_textarea(page)
        filled = False

        if textarea:
            self._narrate(reporter, "   🔹 Filling cover letter...",
                          gui_message="Writing your cover letter into the form…", vacancy_id=vid)
            try:
                cover_letter = llm_cover.cover(vacancy_text, vacancy_id)
                # type() fires React input/change events per-keystroke;
                # textarea stays disabled while empty — events are needed to enable the submit button
                textarea.type(cover_letter, delay=5, timeout=60000)
                filled = True
                # Plain print — folded into the line above for the GUI.
                print("   ✅ Cover letter filled")
                self._wait_and_random_delay(page, 2000, 3000)
            except Exception as e:
                self._narrate(reporter, f"   ⚠️ Textarea fill error: {e}",
                              gui_message="Trouble filling in the cover letter", vacancy_id=vid)
        else:
            # No recognized cover field — genuinely non-canonical: one of
            # HH's own variable screening steps (location/salary/English/
            # relocation/whatever else gets added later). Worth its own
            # distinct, more evocative beat — admitting the code doesn't
            # know this field shape and handing interpretation to the model,
            # not routine mechanics. (llm_agent.py's own call_type tagging
            # covers the matching "reading an unfamiliar
            # question…" narration once fill_form() actually fires.)
            self._narrate(reporter, "   🔎 Unrecognized step — collecting fields for the model",
                          gui_message="Unfamiliar screening step — asking the model how to answer",
                          vacancy_id=vid)
            filled_count, ambiguous_reasons = self._fill_generic_fields(page, vacancy_text)
            if filled_count:
                self._narrate(reporter, f"   ✅ Filled {filled_count} field(s) on this step via LLM",
                              gui_message=f"[OK] answered {filled_count} question(s) on this step",
                              vacancy_id=vid)
            else:
                self._narrate(reporter, "   ⚠️ Cover letter field not found, no other fillable fields either",
                              gui_message="Nothing to fill in on this step", vacancy_id=vid)

        # 2. Click the submit button (wait for it to become enabled after filling)
        nav_button = self._find_nav_button(page)
        if not nav_button:
            return ProcessResult(
                success=False,
                status="skipped_hh_modal",
                reason="Navigation buttons not found in HH modal",
                scenario="hh_modal_error",
                is_terminal=True,
                goal_reached=False
            )

        button_text = nav_button.inner_text().strip()
        print(f"   🔹 Clicking: '{button_text}'")
        nav_button.scroll_into_view_if_needed()
        nav_button.click()
        self._wait_and_random_delay(page, 2000, 4000)

        # 3. Post-submit edge case check
        edge_result = self._check_post_submit_edge_case(page, reporter=reporter, vacancy_id=vid)
        if edge_result:
            return edge_result

        # 4. Cover filled — continue loop so chatik provides the terminal status.
        # Modal is an unstable HH experiment; chatik is the only reliable ground truth.
        if filled:
            result = ProcessResult(
                success=True,
                status="hh_modal_cover_sent",
                reason=f"Cover letter submitted via modal, button: '{button_text}'",
                scenario="hh_modal_with_cover",
                details={'button_text': button_text},
                is_terminal=False,
                goal_reached=False
            )
        else:
            result = ProcessResult(
                success=True,
                status="hh_modal_navigation",
                reason=f"HH modal navigation: '{button_text}'",
                scenario="hh_modal_no_cover",
                details={'button_text': button_text},
                is_terminal=False,
                goal_reached=False
            )

        # An LLM answer for one of this step's generic fields (radio/checkbox/
        # select) that couldn't actually be applied — ambiguous, not a clean
        # "no answer" skip. Surfaced via needs_debug_review, same as
        # questions.py's own equivalent tracking, instead of disappearing
        # into a print().
        if ambiguous_reasons:
            return self._flag_for_debug_review(
                result, "; ".join(ambiguous_reasons), ambiguous_count=len(ambiguous_reasons)
            )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_cover_textarea(self, page):
        """Finds cover letter textarea using verified selectors.

        The modal-specific addresses go first, then the shared cascade — via
        BaseHandler._find_cover_field, which is where the "never an employer's
        question field" rule lives, and which walks EVERY match of each
        selector rather than the first (this method used to hand-roll its own
        copy of the single-match bug).
        """
        return self._find_cover_field(page, extra_selectors=[
            # Popup modal (verified 2026-04-06)
            '[data-qa="vacancy-response-popup-form-letter-input"] textarea',
            SELECTORS['popup_letter_input'],
            # Inline form (verified 2026-04-05)
            '[data-qa="vacancy-response-letter-informer"] textarea',
            '[data-qa="textarea-native-wrapper"] textarea',
            'textarea[data-qa*="response"]',
        ], reject=self._is_salary_field)

    def _is_salary_field(self, element) -> bool:
        """Returns True if the element is a salary field."""
        try:
            placeholder = (element.get_attribute('placeholder') or "").lower()
            salary_keywords = ['зарплат', 'salary', 'ожидани', 'доход', 'желаем']
            return any(kw in placeholder for kw in salary_keywords)
        except Exception:
            return False

    def _fill_generic_fields(self, page, vacancy_text: str) -> tuple[int, list]:
        """Blind field collection for a modal step that has no recognized
        cover-letter field — HH's own variable screening steps (location,
        salary expectation, English level, relocation, and whatever else
        gets added later). Same DOM-only, no-keyword-guessing approach as
        questions.py's collector: gather text/radio/checkbox/select fields
        purely by structure, hand them to one LLM batch call, fill by the
        answer. Returns (count actually filled, ambiguous_reasons) — the
        latter mirrors questions.py's own tracking of an LLM answer that
        couldn't actually be applied (no matching option, element
        interaction threw), surfaced via needs_debug_review by the caller
        instead of disappearing into a print().
        """
        if self._agent is None:
            return 0, []

        fields = []
        ambiguous_reasons: list[str] = []
        text_fields = []       # (idx, element)
        radio_groups = {}      # name → {question, options, elements}
        checkbox_groups = {}   # question_text → {idx, question, elements}
        select_fields = []     # (idx, element)

        inputs = page.query_selector_all('input[type="text"], input[type="radio"], input[type="checkbox"], textarea')
        for i, inp in enumerate(inputs):
            if not inp.is_visible():
                continue
            itype = inp.get_attribute("type") or inp.evaluate("el => el.tagName.toLowerCase()")

            if itype == "radio":
                name = inp.get_attribute("name") or f"unnamed_{i}"
                opt_text = self._extract_radio_option_text(inp)
                val = inp.get_attribute("value") or ""
                if name not in radio_groups:
                    radio_groups[name] = {"question": self._extract_label(inp), "options": [], "elements": []}
                radio_groups[name]["options"].append(opt_text)
                radio_groups[name]["elements"].append((i, inp, val, opt_text))
            elif itype == "checkbox":
                question = self._extract_label(inp)
                option = self._extract_radio_option_text(inp)
                if question:
                    if question not in checkbox_groups:
                        checkbox_groups[question] = {"idx": f"cbgroup_{len(checkbox_groups)}", "question": question, "elements": []}
                    checkbox_groups[question]["elements"].append((i, inp, option or f"option_{i}"))
            else:
                # Salary fields are included here (unlike the cover-textarea
                # path, which deliberately excludes them to avoid cross-
                # filling) — a salary-expectation question is still worth
                # answering through the generic LLM batch.
                label = self._extract_label(inp)
                if label:
                    text_fields.append((i, inp, label))

        for i, sel in enumerate(page.query_selector_all('select')):
            if not sel.is_visible():
                continue
            label = self._extract_label(sel)
            options = [o.inner_text().strip() for o in sel.query_selector_all('option') if o.inner_text().strip()]
            if label and options:
                name = sel.get_attribute("name") or f"select_{i}"
                select_fields.append((i, sel, label, options, name))

        for i, _, label in text_fields:
            fields.append({"idx": str(i), "label": label, "type": "text"})
        for name, grp in radio_groups.items():
            if grp["question"]:
                fields.append({"idx": f"radio_{name}", "label": grp["question"], "type": "radio_group", "options": grp["options"]})
        for question, grp in checkbox_groups.items():
            if len(grp["elements"]) == 1:
                i, _, _ = grp["elements"][0]
                fields.append({"idx": f"checkbox_{i}", "label": question, "type": "checkbox"})
            else:
                fields.append({"idx": grp["idx"], "label": question, "type": "checkbox_group",
                                "options": [opt for _, _, opt in grp["elements"]]})
        for i, _, label, options, _name in select_fields:
            fields.append({"idx": f"select_{i}", "label": label, "type": "select", "options": options})

        if not fields:
            return 0, []

        if self._agent is None:
            print("   ⚠️ LLM unavailable — cannot answer this modal's fields")
            return 0, []
        try:
            answers = self._agent.fill_form(vacancy_text, fields)
        except Exception as e:
            print(f"   ⚠️ LLM fill_form error: {e}")
            return 0, []

        filled_count = 0

        for i, inp, label in text_fields:
            answer = answers.get(str(i), "")
            if not answer:
                continue
            try:
                inp.type(answer, delay=10)
                filled_count += 1
                self._wait_and_random_delay(page, 400, 800)
            except Exception as e:
                print(f"   ⚠️ Text field error: {e}")

        for name, grp in radio_groups.items():
            answer = answers.get(f"radio_{name}", "").strip()
            if not answer:
                continue
            # "open: <free text>" matches the option whose HTML value=="open"
            # (the actual "Свой вариант" input), not its display text — a
            # freeform answer will essentially never equal an option's own
            # label verbatim. Mirrors questions.py's real, working logic.
            free_text = None
            if answer.lower().startswith("open:"):
                free_text = answer[5:].strip()
                target = "open"
            else:
                target = answer.strip().lower()
            clicked = False
            match_found = False
            for idx, el, val, opt_text in grp["elements"]:
                is_open = val == "open"
                matches_open = is_open and free_text is not None
                matches_text = not is_open and opt_text.strip().lower() == target
                if matches_open or matches_text:
                    match_found = True
                    try:
                        el.click()
                        self._wait_and_random_delay(page, 400, 800)
                        if matches_open and free_text:
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
                            else:
                                ambiguous_reasons.append(f"radio_open_no_textarea[{name}]")
                        filled_count += 1
                        clicked = True
                    except Exception as e:
                        print(f"   ⚠️ Radio click error: {e}")
                        ambiguous_reasons.append(f"radio_click_error[{name}]: {e}")
                    break
            if not clicked and not match_found:
                ambiguous_reasons.append(f"radio_no_match[{name}]: '{answer[:60]}'")

        for question, grp in checkbox_groups.items():
            elems = grp["elements"]
            if len(elems) == 1:
                i, inp, _ = elems[0]
                answer = answers.get(f"checkbox_{i}", "").strip().lower()
                if answer.startswith(("yes", "да")):
                    try:
                        inp.check()
                        filled_count += 1
                        self._wait_and_random_delay(page, 400, 800)
                    except Exception as e:
                        print(f"   ⚠️ Checkbox error: {e}")
                        ambiguous_reasons.append(f"checkbox_error[{question[:30]}]: {e}")
            else:
                answer = answers.get(grp["idx"], "").strip()
                if not answer:
                    continue
                # Same "Свой вариант" recognition as questions.py — checkbox
                # groups don't carry a value=="open" attribute the way radio
                # inputs do, so this matches by the option's own display text.
                free_text = None
                if answer.lower().startswith("open:"):
                    free_text = answer[5:].strip()
                    target = "open"
                else:
                    target = answer.strip().lower()
                clicked = False
                match_found = False
                for i, inp, opt_text in elems:
                    norm_opt = opt_text.strip().lower()
                    is_free = norm_opt in ("свой вариант", "другое", "other")
                    matches_free = is_free and (free_text is not None or target in ("свой вариант", "другое", "other"))
                    matches_opt = not is_free and norm_opt == target
                    if matches_free or matches_opt:
                        match_found = True
                        try:
                            inp.check()
                            self._wait_and_random_delay(page, 400, 800)
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
                                    else:
                                        ambiguous_reasons.append(f"checkbox_group_open_no_textarea[{question[:30]}]")
                                except Exception as e:
                                    ambiguous_reasons.append(f"checkbox_group_open_textarea_error[{question[:30]}]: {e}")
                            filled_count += 1
                            clicked = True
                        except Exception as e:
                            print(f"   ⚠️ Checkbox group error: {e}")
                            ambiguous_reasons.append(f"checkbox_group_click_error[{question[:30]}]: {e}")
                        break
                if not clicked and not match_found:
                    ambiguous_reasons.append(f"checkbox_group_no_match[{question[:30]}]: '{answer[:60]}'")

        for i, sel, label, options, name in select_fields:
            answer = answers.get(f"select_{i}", "").strip()
            if not answer:
                continue
            # Some selects carry a "Свой ответ"/"другое" option that reveals
            # a hidden text field, same mechanism as radio_group's "Свой
            # вариант" — reuse that exact convention rather than treating
            # select as having no free-text escape at all.
            free_text = None
            if answer.lower().startswith("open:"):
                free_text = answer[5:].strip()
                custom_option = next(
                    (o for o in options if o.strip().lower() in ("свой ответ", "свой вариант", "другое", "other")),
                    None
                )
                target_label = custom_option or answer
            else:
                target_label = answer
            try:
                sel.select_option(label=target_label)
                filled_count += 1
                self._wait_and_random_delay(page, 400, 800)
                if free_text:
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
                    else:
                        ambiguous_reasons.append(f"select_open_no_textarea[{label[:30]}]")
            except Exception as e:
                print(f"   ⚠️ Select field error: {e}")
                ambiguous_reasons.append(f"select_no_match[{label[:30]}]: '{answer[:60]}'")

        return filled_count, ambiguous_reasons

    def _extract_label(self, inp) -> str:
        """Finds the human-readable question/label text for a field."""
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
            return (inp.get_attribute("placeholder") or inp.get_attribute("aria-label") or "")[:200]
        except Exception as e:
            # An empty label does not just mean "unlabelled": the caller drops
            # any field whose label is empty (`if label:`), so an extraction
            # crash makes the field vanish from the LLM batch entirely, never
            # gets answered, and — if it was required — resurfaces after submit
            # as "a required field failed validation", blaming the model for an
            # answer it was never asked to give.
            print(f"   ⚠️ Couldn't read a field's label ({e}) — the field will be skipped")
            return ""

    def _extract_radio_option_text(self, inp) -> str:
        """Finds the visible option text for a radio/checkbox input."""
        try:
            return inp.evaluate("""el => {
                const norm = s => s.replace(/ /g, ' ').trim();
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

    def _find_nav_button(self, page):
        """Finds the navigation button ('Submit', 'Apply', 'Next', etc.).

        Popup button starts disabled — the address tier waits up to 5s for it
        to become enabled after the form is filled.

        Both tiers, the wait, the dialog scoping and the survey veto live in
        BaseHandler._find_action_button — read its docstring for why the
        keyword tier is still here and what it is no longer allowed to do.
        On 2026-08-11 this method's page-wide keyword scan clicked
        "Сохранить и продолжить" on one of hh's profile surveys.
        """
        btn, how = self._find_action_button(
            page,
            addresses=[SELECTORS['popup_submit'], SELECTORS['letter_submit']],
            keywords=FORM_KEYWORDS['navigation'] + ['откликнуться'],
        )
        if btn is not None and how == "wording":
            print("   ℹ️ Navigation button matched by wording, not by address — "
                  "hh ships no data-qa on this one")
        return btn

    def verify_submission(self, page) -> bool:
        return self._poll_for_success(page, timeout_s=5)

    def _check_post_submit_edge_case(self, page, reporter=None, vacancy_id=None) -> ProcessResult | None:
        """
        Checks post-submit edge case: 'Application already viewed by employer' → click 'Chat'.

        Selectors verified 2026-04-05 via debug snapshots.
        """
        try:
            error_el = page.query_selector(SELECTORS['form_error'])
            if not error_el or not error_el.is_visible():
                return None

            error_text = error_el.inner_text().strip()
            # Plain print — the two branches below narrate the outcome that
            # matters; the raw error string itself is diagnostic detail.
            print(f"   ⚠️ Form error detected: '{error_text}'")

            # "Please fill in cover letter" — textarea was not filled
            if 'введите' in error_text.lower() or 'заполните' in error_text.lower():
                self._narrate(reporter, f"   ⚠️ Cover letter not filled: {error_text}",
                              gui_message="[BLCK] the form rejected my submission — cover letter didn't go through",
                              vacancy_id=vacancy_id)
                return ProcessResult(
                    success=False,
                    status="skipped_no_cover_filled",
                    reason=f"Cover letter not filled: {error_text}",
                    scenario="hh_modal_cover_required",
                    is_terminal=True,
                    goal_reached=False
                )

            if 'просмотрен' not in error_text.lower() and 'уже' not in error_text.lower():
                return None

            self._narrate(reporter, "   🔍 Edge case: application already viewed — looking for 'Chat' button...",
                          gui_message="This vacancy was already viewed — the employer wants to chat instead",
                          vacancy_id=vacancy_id)

            chat_link = find_chat_link(page)
            if chat_link is not None:
                # Plain print — folded into the line above for the GUI.
                print("   🔹 Clicking 'Chat'...")
                chat_link.click()
                self._wait_and_random_delay(page, 2000, 3000)
                return ProcessResult(
                    success=True,
                    status="chat_redirect",
                    reason=f"Edge case: {error_text} → redirected to chat",
                    scenario="edge_case_chat",
                    details={'error_text': error_text},
                    is_terminal=False,
                    goal_reached=False
                )

            return ProcessResult(
                success=False,
                status="skipped_edge_case_no_chat",
                reason=f"Edge case: {error_text}, chat button not found",
                scenario="edge_case_no_chat",
                details={'error_text': error_text},
                is_terminal=True,
                goal_reached=False
            )

        except Exception as e:
            print(f"   ⚠️ Edge case check error: {e}")
            return None
