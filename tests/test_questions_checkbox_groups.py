"""A checkbox group is multi-select, and QuestionsHandler must treat it as one.

Until 2026-09-09 it did not: the group loop was written as "pick exactly one
option" and compared the whole answer string against each option. On a live
form that lost the entire application — the model answered a "выберите не более
3-х вариантов" question with three options, nothing matched, nothing was ticked,
and hh rejected a form whose other twelve answers were already filled in. Two
other groups on the same page came out worse and silently: the model prefixed
its list with "open:", so the handler ticked "Свой вариант" and typed hh's own
option names into the comment box, which passes validation and logs as success.

The fixtures here are that form's option lists, kept verbatim because two of
the traps are in the option TEXT itself: "Цифровые продукты (SaaS, сервисы,
приложения)" and "Одежда, обувь, аксессуары" carry the commas that make a
comma-splitting matcher wrong.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        from adapters.hh.handlers.questions import QuestionsHandler
        from adapters.hh.handlers.base import (ProcessResult, choose_checkbox_options,
                                               coerce_answers)

# ── the live form's own option lists ─────────────────────────────────────────
CATEGORIES = [  # "…не более 3-х вариантов" — the group that killed the application
    "Промышленное оборудование и станки", "Электроинструмент и ручной инструмент",
    "Товары для строительства и ремонта (DIY)", "Автотовары и запчасти",
    "Электроника и бытовая техника", "Товары для дома и сада", "Мебель и интерьер",
    "Детские товары", "Одежда, обувь, аксессуары", "Косметика и парфюмерия",
    "Продукты питания (FMCG)", "Другое", "Свой вариант",
]
WORKED_WITH = [  # holds an option that contains commas of its own
    "Техника / электроника", "Инструменты / оборудование", "DIY", "Автотовары",
    "Бытовая техника", "Строительные товары",
    "Цифровые продукты (SaaS, сервисы, приложения)", "Товары для дома",
    "Другое (напишу в комментариях)", "Свой вариант",
]
PREPARE = [
    "Анализ рынка", "Исследование клиентов", "Анализ конкурентов", "Unit-экономику",
    "Финансовые расчеты", "Анализ рисков", "Рекомендации по внедрению",
    "Обычно достаточно самой идеи", "Свой вариант",
]

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


# ── the matcher, on its own ───────────────────────────────────────────────────
print("choose_checkbox_options")

picked, free = choose_checkbox_options(
    "Электроинструмент и ручной инструмент, Товары для строительства и ремонта (DIY), "
    "Автотовары и запчасти", CATEGORIES)
check("the answer that lost the application resolves to its three options",
      picked == [1, 2, 3] and free is None)

picked, free = choose_checkbox_options(
    ["Мебель и интерьер", "Детские товары"], CATEGORIES)
check("a JSON array — the declared format — resolves", picked == [6, 7] and free is None)

picked, free = choose_checkbox_options(
    '["Мебель и интерьер", "Детские товары"]', CATEGORIES)
check("an array the model sent as a string still resolves", picked == [6, 7] and free is None)

picked, free = choose_checkbox_options("Одежда, обувь, аксессуары", CATEGORIES)
check("an option whose own text has commas stays one option, not three",
      picked == [8] and free is None)

picked, free = choose_checkbox_options(
    "Цифровые продукты (SaaS, сервисы, приложения)", WORKED_WITH)
check("…and again with parentheses around the commas", picked == [6] and free is None)

picked, free = choose_checkbox_options(
    "open: Анализ рынка, Исследование клиентов, Анализ конкурентов, Unit-экономику, "
    "Финансовые расчеты, Анализ рисков, Рекомендации по внедрению", PREPARE)
check("option names listed after 'open:' are ticked, not recited as free text",
      picked == [0, 1, 2, 3, 4, 5, 6] and free is None)

picked, free = choose_checkbox_options("open: ничего из перечисленного", CATEGORIES)
check("a genuinely custom answer comes back as free text",
      picked == [] and free == "ничего из перечисленного")

picked, free = choose_checkbox_options("", CATEGORIES)
check("no answer is neither options nor free text", picked == [] and free is None)

picked, free = choose_checkbox_options("DIY", WORKED_WITH)
check("a short option matches on a word boundary", picked == [2] and free is None)

check("coerce_answers leaves a checkbox group's list alone",
      coerce_answers({"cbgroup_0": ["a", "b"]})["cbgroup_0"] == ["a", "b"])
check("coerce_answers flattens a list sent for something that takes a string",
      coerce_answers({"radio_x": ["a", "b"]})["radio_x"] == "a, b")


# ── through the handler ───────────────────────────────────────────────────────
print("\nQuestionsHandler.process — the group loop")


class FakeCheckbox:
    def __init__(self, question, option, name, refuses=False):
        self.question, self.option, self.name = question, option, name
        self.checked = False
        self._refuses = refuses

    def is_visible(self):
        return True

    def get_attribute(self, n):
        return {"type": "checkbox", "name": self.name}.get(n)

    def evaluate(self, js):
        return self.option if "cell" in js else self.question

    def evaluate_handle(self, js):
        return FakeHandle(None)

    def query_selector(self, s):
        return None

    def check(self):
        if not self._refuses:
            self.checked = True

    def is_checked(self):
        return self.checked


class FakeTextarea:
    def __init__(self):
        self.typed = None

    def is_visible(self):
        return True

    def type(self, text, delay=10):
        self.typed = text


class FakeHandle:
    def __init__(self, el):
        self._el = el

    def as_element(self):
        return self._el


class FakePage:
    def __init__(self, inputs, textarea=None):
        self._inputs, self._textarea = inputs, textarea

    def query_selector_all(self, s):
        return [] if s == "select" else self._inputs

    def query_selector(self, s):
        return self._textarea

    def wait_for_timeout(self, ms):
        pass


def run(question, options, answer, name="task_1", refuse_after=None, textarea=None):
    """One group on a page, one answer, returns (elements, ProcessResult)."""
    els = [FakeCheckbox(question, o, name,
                        refuses=(refuse_after is not None and i >= refuse_after))
           for i, o in enumerate(options)]
    page = FakePage(els, textarea)
    h = QuestionsHandler.__new__(QuestionsHandler)
    h._agent = type("A", (), {"fill_form": staticmethod(lambda v, f: {"cbgroup_0": answer})})()
    h._submit = lambda p, filled, total: ProcessResult(
        success=True, status="applied", reason="", details={"filled_count": filled})
    h._wait_and_random_delay = lambda p, a, b: None
    return els, h.process(page, vacancy_text="v")


def ticked(els):
    return [e.option for e in els if e.checked]


def reason(res):
    return (res.details or {}).get("debug_reason", "")


els, res = run("Какие категории товаров Вам наиболее интересны? (не более 3-х вариантов)",
               CATEGORIES,
               "Электроинструмент и ручной инструмент, Товары для строительства и ремонта (DIY), "
               "Автотовары и запчасти")
check("three options asked for, three ticked", len(ticked(els)) == 3)
check("…and the group is not flagged", res.status == "applied", )

els, res = run("С какими категориями товаров Вы работали?", WORKED_WITH,
               "Цифровые продукты (SaaS, сервисы, приложения)")
check("a comma-bearing single option ticks exactly one box",
      ticked(els) == ["Цифровые продукты (SaaS, сервисы, приложения)"])

ta = FakeTextarea()
els, res = run("Что Вы обычно готовите?", PREPARE,
               "open: Анализ рынка, Исследование клиентов, Анализ конкурентов", textarea=ta)
check("'open:' over real option names ticks them and writes nothing",
      ticked(els) == ["Анализ рынка", "Исследование клиентов", "Анализ конкурентов"]
      and ta.typed is None)

ta = FakeTextarea()
els, res = run("Что Вы обычно готовите?", PREPARE,
               "open: сначала считаю юнит-экономику руками", textarea=ta)
check("a genuinely custom answer ticks 'Свой вариант' and types itself",
      ticked(els) == ["Свой вариант"] and ta.typed == "сначала считаю юнит-экономику руками")

els, res = run("Какие категории товаров?", CATEGORIES,
               "Электроинструмент и ручной инструмент, Товары для строительства и ремонта (DIY), "
               "Автотовары и запчасти", refuse_after=3)
check("a tick the page refuses stops the loop instead of pretending", len(ticked(els)) == 2)
check("…and says how far it got", "checkbox_group_tick_refused" in reason(res))

els, res = run("Какие категории товаров?", CATEGORIES, "")
check("a group with no answer is reported, not silently skipped",
      "checkbox_group_no_answer" in reason(res))

no_free = [o for o in PREPARE if o != "Свой вариант"]
els, res = run("Что Вы обычно готовите?", no_free, "open: что-то своё")
check("nothing matched and no free-text option → no_match, and no box invented",
      "checkbox_group_no_match" in reason(res) and ticked(els) == [])

els, res = run("Какие категории товаров?", CATEGORIES,
               "Электроинструмент и ручной инструмент")
check("the single-option answer that used to be the only working shape still works",
      ticked(els) == ["Электроинструмент и ручной инструмент"] and res.status == "applied")

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
