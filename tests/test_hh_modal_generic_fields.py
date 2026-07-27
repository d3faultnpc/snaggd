"""
Unit tests for HHModalHandler._fill_generic_fields() — the new blind-field
fallback added session 56 for hh_modal.py's variable screening steps
(location, salary expectation, English level, relocation, etc.) that have no
recognized native cover-letter textarea. Confirmed live 2026-07-26: these
steps were previously silently skipped entirely (no code path handled them).

Also covers the radio_group/checkbox_group "Свой вариант" (open-ended
free-text) path — code-reviewer caught this session: the first version
matched a freeform "open: ..." answer against each option's own DISPLAY TEXT
instead of the option carrying value=="open" (radio) / its "Свой вариант"
option text (checkbox), so it could never actually match — fixed to mirror
questions.py's real, working logic. These specific cases would have failed
against that first version; they exist to keep it that way.

Uses lightweight mock Playwright elements — no real browser.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        from adapters.hh.handlers.hh_modal import HHModalHandler
        import adapters.hh.handlers.hh_modal as hh_modal_module


class FakeElement:
    """label = the group question (_extract_label's task-body probe).
    option_text = this specific element's own visible option text
    (_extract_radio_option_text's cell/label probe) — defaults to label for
    plain single fields where the two are the same thing."""

    def __init__(self, label="", option_text=None, tag="input", itype="text",
                 value="", options=None, name="", visible=True):
        self._label = label
        self._option_text = option_text if option_text is not None else label
        self._tag = tag
        self._itype = itype
        self._value = value
        self._options = options or []
        self._name = name
        self._visible = visible
        self._revealed_textarea = None  # set externally for checkbox "Свой вариант" cases
        self.typed = None
        self.clicked = False
        self.checked = False
        self.selected_label = None

    def is_visible(self):
        return self._visible

    def get_attribute(self, name):
        if name == "type":
            return self._itype if self._tag == "input" else None
        if name == "value":
            return self._value
        if name == "name":
            return self._name
        return None

    def evaluate(self, js):
        if "tagName" in js:
            return self._tag
        if "cell" in js:
            # _extract_radio_option_text's probe — this element's own option text
            return self._option_text
        # _extract_label's task-body probe — the shared group question
        return self._label

    def evaluate_handle(self, js):
        return FakeElementHandle(self._revealed_textarea)

    def query_selector(self, selector):
        return None

    def query_selector_all(self, selector):
        if selector == "option" and self._tag == "select":
            return [FakeOption(o) for o in self._options]
        return []

    def type(self, text, delay=10, timeout=None):
        self.typed = text

    def click(self):
        self.clicked = True

    def check(self):
        self.checked = True

    def select_option(self, label=None):
        self.selected_label = label


class FakeOption:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class FakeElementHandle:
    def __init__(self, el):
        self._el = el

    def as_element(self):
        return self._el


class FakePage:
    """inputs/selects feed query_selector_all; hidden_textarea (radio "Свой
    вариант" reveal) is returned by wait_for_selector/query_selector once
    "revealed" (simulating the animated textarea HH shows after the click)."""

    def __init__(self, inputs=None, selects=None, hidden_textarea=None):
        self._inputs = inputs or []
        self._selects = selects or []
        self._hidden_textarea = hidden_textarea

    def query_selector_all(self, selector):
        if selector == "select":
            return self._selects
        return self._inputs

    def wait_for_selector(self, selector, state="visible", timeout=None):
        if self._hidden_textarea is None:
            raise TimeoutError("no textarea configured")

    def query_selector(self, selector):
        return self._hidden_textarea


results = []


def check(label, condition):
    status = "✅" if condition else "❌"
    print(f"  {status} {label}")
    results.append(bool(condition))


handler = HHModalHandler()

# ── A text field + a select dropdown, both answered by the mocked LLM ──
location_select = FakeElement(label="Локация", tag="select",
                               options=["Москва", "Санкт-Петербург", "Удалённо"])
salary_text = FakeElement(label="Ожидаемая зарплата", tag="input", itype="text")
page = FakePage(inputs=[salary_text], selects=[location_select])

fake_agent = MagicMock()
fake_agent.fill_form.return_value = {"0": "250000", "select_0": "Удалённо"}
with patch.object(hh_modal_module, "_agent", fake_agent):
    filled, ambiguous = handler._fill_generic_fields(page, "some vacancy text")

check("_fill_generic_fields fills both fields (text + select)", filled == 2)
check("no ambiguous reasons on a clean fill", ambiguous == [])
check("text field got typed with the LLM's answer", salary_text.typed == "250000")
check("select field got select_option called with the LLM's answer",
      location_select.selected_label == "Удалённо")

fill_form_call = fake_agent.fill_form.call_args
sent_fields = fill_form_call[0][1]
select_spec = next((f for f in sent_fields if f["idx"] == "select_0"), None)
check("select field spec sent to fill_form() has type='select'",
      select_spec is not None and select_spec["type"] == "select")
check("select field spec includes the real dropdown options",
      select_spec is not None and select_spec["options"] == ["Москва", "Санкт-Петербург", "Удалённо"])

# ── A field the LLM has no answer for is left untouched, not crashed on ──
unanswered = FakeElement(label="Готовность к переезду", tag="input", itype="text")
page2 = FakePage(inputs=[unanswered], selects=[])
fake_agent2 = MagicMock()
fake_agent2.fill_form.return_value = {}  # empty answer
with patch.object(hh_modal_module, "_agent", fake_agent2):
    filled2, ambiguous2 = handler._fill_generic_fields(page2, "some vacancy text")
check("field with no LLM answer is left untouched", unanswered.typed is None)
check("filled count is 0 when nothing was answered", filled2 == 0)
check("no answer at all is not itself flagged ambiguous", ambiguous2 == [])

# ── No fields at all on the page: returns (0, []), never calls the LLM ──
empty_page = FakePage(inputs=[], selects=[])
fake_agent3 = MagicMock()
with patch.object(hh_modal_module, "_agent", fake_agent3):
    filled3, ambiguous3 = handler._fill_generic_fields(empty_page, "some vacancy text")
check("no fields on page → returns 0", filled3 == 0)
check("no fields on page → returns no ambiguous reasons", ambiguous3 == [])
check("no fields on page → fill_form never called", fake_agent3.fill_form.call_count == 0)

# ── _agent unavailable (LLM down): returns (0, []) immediately, no crash ──
with patch.object(hh_modal_module, "_agent", None):
    filled4, ambiguous4 = handler._fill_generic_fields(page, "some vacancy text")
check("LLM unavailable → returns 0 without raising", filled4 == 0)

# ── Radio group: direct option match (no "Свой вариант" involved) ──
radio_yes = FakeElement(label="Готовы к командировкам?", option_text="Да",
                         tag="input", itype="radio", value="yes", name="travel")
radio_no = FakeElement(label="Готовы к командировкам?", option_text="Нет",
                        tag="input", itype="radio", value="no", name="travel")
radio_open = FakeElement(label="Готовы к командировкам?", option_text="Свой вариант",
                          tag="input", itype="radio", value="open", name="travel")
radio_page = FakePage(inputs=[radio_yes, radio_no, radio_open], selects=[])
fake_agent_radio = MagicMock()
fake_agent_radio.fill_form.return_value = {"radio_travel": "Да"}
with patch.object(hh_modal_module, "_agent", fake_agent_radio):
    filled_r, ambiguous_r = handler._fill_generic_fields(radio_page, "some vacancy text")
check("radio group: direct match clicks the right option", radio_yes.clicked is True)
check("radio group: direct match doesn't click other options", radio_no.clicked is False)
check("radio group: direct match doesn't click the open option", radio_open.clicked is False)
check("radio group: direct match filled, no ambiguous reasons", filled_r == 1 and ambiguous_r == [])

# ── Radio group: "open: <free text>" — THE bug code-reviewer caught. Must
# match the option whose value=="open" and type into the revealed textarea,
# not match the freeform text against any option's own display text. ──
radio_yes2 = FakeElement(label="Готовность к переезду?", option_text="Да",
                          tag="input", itype="radio", value="yes", name="relocate")
radio_open2 = FakeElement(label="Готовность к переезду?", option_text="Свой вариант",
                           tag="input", itype="radio", value="open", name="relocate")
revealed_ta = FakeElement(tag="textarea")
radio_page2 = FakePage(inputs=[radio_yes2, radio_open2], selects=[], hidden_textarea=revealed_ta)
fake_agent_radio2 = MagicMock()
fake_agent_radio2.fill_form.return_value = {"radio_relocate": "open: готов при полной удалёнке"}
with patch.object(hh_modal_module, "_agent", fake_agent_radio2):
    filled_r2, ambiguous_r2 = handler._fill_generic_fields(radio_page2, "some vacancy text")
check("radio 'open:' answer clicks the value==open option, not a text-match guess",
      radio_open2.clicked is True and radio_yes2.clicked is False)
check("radio 'open:' answer types the free text into the revealed textarea",
      revealed_ta.typed == "готов при полной удалёнке")
check("radio 'open:' path filled, no ambiguous reasons", filled_r2 == 1 and ambiguous_r2 == [])

# ── Radio group: no matching option at all → tracked as ambiguous, not silently dropped ──
radio_only_yes = FakeElement(label="Есть загранпаспорт?", option_text="Да",
                              tag="input", itype="radio", value="yes", name="passport")
radio_page3 = FakePage(inputs=[radio_only_yes], selects=[])
fake_agent_radio3 = MagicMock()
fake_agent_radio3.fill_form.return_value = {"radio_passport": "не указано в анкете"}
with patch.object(hh_modal_module, "_agent", fake_agent_radio3):
    filled_r3, ambiguous_r3 = handler._fill_generic_fields(radio_page3, "some vacancy text")
check("radio group no-match: nothing clicked", radio_only_yes.clicked is False)
check("radio group no-match: surfaced as ambiguous, not silently dropped",
      any("radio_no_match" in reason for reason in ambiguous_r3))

# ── Checkbox group (mutually exclusive, multi-option): "Свой вариант" free text ──
revealed_ta2 = FakeElement(tag="textarea")
cb_a = FakeElement(label="Уровень английского", option_text="A1-A2",
                    tag="input", itype="checkbox", value="a")
cb_b = FakeElement(label="Уровень английского", option_text="B1-B2",
                    tag="input", itype="checkbox", value="b")
cb_open = FakeElement(label="Уровень английского", option_text="Свой вариант",
                       tag="input", itype="checkbox", value="open")
cb_open._revealed_textarea = revealed_ta2
cb_page = FakePage(inputs=[cb_a, cb_b, cb_open], selects=[])
fake_agent_cb = MagicMock()
fake_agent_cb.fill_form.return_value = {"cbgroup_0": "open: intermediate, могу проходить интервью"}
with patch.object(hh_modal_module, "_agent", fake_agent_cb):
    filled_cb, ambiguous_cb = handler._fill_generic_fields(cb_page, "some vacancy text")
check("checkbox group 'Свой вариант' checks the right box", cb_open.checked is True)
check("checkbox group 'Свой вариант' doesn't check the preset options",
      cb_a.checked is False and cb_b.checked is False)
check("checkbox group 'Свой вариант' types free text into the revealed textarea",
      revealed_ta2.typed == "intermediate, могу проходить интервью")
check("checkbox group 'Свой вариант' path filled, no ambiguous reasons",
      filled_cb == 1 and ambiguous_cb == [])

# ── Select with нет/да/свой ответ — user's own live scenario (session 56):
# selects can ALSO reveal a hidden text field on a custom-answer option, same
# mechanism as radio_group's "Свой вариант", not "no escape hatch at all". ──
revealed_ta3 = FakeElement(tag="textarea")
relocation_select = FakeElement(label="Готовы к переезду?",
                                 tag="select", name="relocation_select",
                                 options=["нет", "да", "свой ответ"])
select_page = FakePage(inputs=[], selects=[relocation_select], hidden_textarea=revealed_ta3)
fake_agent_select = MagicMock()
fake_agent_select.fill_form.return_value = {"select_0": "open: готов при переезде за счёт компании"}
with patch.object(hh_modal_module, "_agent", fake_agent_select):
    filled_s, ambiguous_s = handler._fill_generic_fields(select_page, "some vacancy text")
check("select 'open:' answer selects the 'свой ответ' option itself, not the freeform text",
      relocation_select.selected_label == "свой ответ")
check("select 'open:' answer types the free text into the revealed textarea",
      revealed_ta3.typed == "готов при переезде за счёт компании")
check("select 'open:' path filled, no ambiguous reasons", filled_s == 1 and ambiguous_s == [])

print()
total = len(results)
passed = sum(results)
print(f"{passed}/{total} passed")
if passed != total:
    sys.exit(1)
