"""An option is matched by what it says, not by which alphabet it was typed in.

Three option_match jams on record, three different shapes:
  2026-09-10 #8  — the employer wrote a language scale "А1 А2 В1 В2" in
                   Cyrillic; the model answered "B2" in Latin. Same option to
                   every reader, different strings to ==.
  2026-09-08     — the model wrote "открыто: Готов на удалённую…": the open:
                   form the prompt asks for, in its own language.
  2026-09-09     — "Свой вариант" with nothing behind it (score
                   88). No content to recover; stays a jam, honestly.

The first two are canon now. norm_option folds Cyrillic look-alikes to Latin
for COMPARISON only; open_answer accepts the prefix in the spellings a model
has used. Nothing typed or clicked passes through either.
"""
import sys
from pathlib import Path

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

from adapters.hh.handlers.base import (choose_checkbox_options, is_free_text_option,
                                       norm_option, open_answer)

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


# 2026-09-10 #8, verbatim: Cyrillic options, Latin answer.
options = ["А1", "А2", "В1", "В2", "Свой вариант"]   # А/В are U+0410/U+0412
check("Latin 'B2' matches the Cyrillic 'В2' the employer wrote",
      any(norm_option(o) == norm_option("B2") for o in options[:4]))
check("and it is the right one, not merely some one",
      [norm_option(o) == norm_option("B2") for o in options[:4]] == [False, False, False, True])
check("the fold reaches checkbox groups through the same helper",
      choose_checkbox_options(["B2"], options) == ([3], None))
check("the free-text option is still recognised after the fold",
      is_free_text_option("Свой вариант") and is_free_text_option("Другое") and not is_free_text_option("Да"))
check("plain Cyrillic options still match each other",
      norm_option("Нет") == norm_option("нет") and norm_option("Офис ") == norm_option("офис"))

# 2026-09-08, verbatim: the prefix in Russian.
check("'открыто:' is read as 'open:'",
      open_answer("открыто: Готов на удалённую работу в команду с пересечением") ==
      "Готов на удалённую работу в команду с пересечением")
check("'open:' still works, with or without the space", open_answer("open:x") == "x" and open_answer("open: x") == "x")
check("an answer that is not the open form is None, not empty", open_answer("B2") is None)

# 2026-09-09, verbatim: the literal option name with nothing behind it.
check("'Свой вариант' alone is not an open answer — there is nothing to type", open_answer("Свой вариант") is None)
check("and it does not match any real option either",
      not any(norm_option(o) == norm_option("Свой вариант") for o in options[:4]))

# 2026-09-15 #10: a question that said "можно выбрать несколько" was drawn as a
# radio group, and the answer followed the words instead of the control. The
# prompt now says which of the two the form obeys — as a rule, not an example.
_prompt = (_ENGINE / "prompts" / "form_fill.md").read_text(encoding="utf-8")
check("the prompt says the field's type outranks the question's wording",
      "the type wins" in _prompt and "exactly ONE option even where the question invites several" in _prompt)
check("in both directions, so it is a rule and not a patch",
      "takes several even where the question reads as one choice" in _prompt)
check("and carries no example drawn from a real employer's form",
      "аналитик" not in _prompt and "несколько вариантов" not in _prompt)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
