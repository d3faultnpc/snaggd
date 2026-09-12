"""What the model answered to an employer's questions is kept, in a debug run.

The cover letter has been on the record since it is what went out under the
person's name. The answers to an employer's own questions are the same kind of
thing and left no trace: the console printed the labels, a page snapshot does
not carry React-controlled values, the journal has no field. On 2026-09-11 five
answers went to an employer and the only way to read one back was the
employer's page — where "remote only" had been written about a person whose
profile never says so.

Debug only, beside the snapshots, appended per questionnaire layer. Not in
applied_log.json: that file is for what happened to the application.
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

from adapters.hh.handlers.base import record_answers

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


FIELDS = [
    {"idx": 0, "label": "1) Где вы сейчас находитесь?", "type": "textarea"},
    {"idx": "radio_1", "label": "Формат", "type": "radio_group", "options": ["офис", "удалённо"]},
]

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "07_Vacancy"
    with redirect_stdout(io.StringIO()):
        record_answers(out, "employer_questions", FIELDS, {"0": "Москва", "radio_1": "удалённо"})
    saved = json.loads((out / "answers.json").read_text(encoding="utf-8"))
    check("one entry per questionnaire layer", len(saved) == 1 and saved[0]["form"] == "employer_questions")
    check("each question keeps its label, type and options beside the answer",
          saved[0]["questions"][1]["options"] == ["офис", "удалённо"]
          and saved[0]["questions"][1]["answer"] == "удалённо")
    check("an unanswered question is recorded as empty, not dropped",
          [q["answer"] for q in saved[0]["questions"]] == ["Москва", "удалённо"])

    with redirect_stdout(io.StringIO()):
        record_answers(out, "hh_modal", FIELDS[:1], {})
    saved = json.loads((out / "answers.json").read_text(encoding="utf-8"))
    check("a second layer on the same vacancy is appended, not overwritten",
          len(saved) == 2 and saved[1]["form"] == "hh_modal" and saved[1]["questions"][0]["answer"] == "")

    buf = io.StringIO()
    with redirect_stdout(buf):
        record_answers(None, "employer_questions", FIELDS, {"0": "x"})
    check("without a debug directory nothing is written and nothing is said",
          buf.getvalue() == "")

    buf = io.StringIO()
    with redirect_stdout(buf):
        record_answers(Path(tmp) / "file-not-dir.txt" / "x", "employer_questions",
                       [{"idx": 0, "label": "q", "type": "text", "bad": object()}], {"0": "a"})
    check("a record that cannot be written is reported and never raises",
          "answers record failed" in buf.getvalue())

# Both fill sites hand the model's answers to the recorder.
for rel in ("adapters/hh/handlers/questions.py", "adapters/hh/handlers/hh_modal.py"):
    src = (_ENGINE / rel).read_text(encoding="utf-8")
    check(f"{rel} records what fill_form answered", "record_answers(" in src)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
