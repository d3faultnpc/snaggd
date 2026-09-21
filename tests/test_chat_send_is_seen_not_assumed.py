"""A cover letter counts as sent when its words are in the chat, not when
the send gesture returned.

2026-09-19, vacancy #33: the letter was typed, Enter was pressed, the record
said `applied_via_chat` with a 738-character letter. The screenshot showed
the cover field open and empty and hh's own "no cover letter" bubble
untouched. The send helper cannot return anything but True, and the
verification read an empty input as "React cleared it after the send" —
a success predicate by absence, and the field was empty because the text
had never stayed in it.

Pinned here: the letter is confirmed by its own opening words in a message
bubble of this chat; an empty field is not evidence; when the words are
not there and the field is there and empty, the letter is typed and sent
once more; when they are still not there, the record says so.
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

from adapters.hh.handlers.chat import ChatHandler
from config import SELECTORS

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


class _El:
    def __init__(self, text, visible=True, value=""):
        self._text, self._visible, self._value = text, visible, value
    def inner_text(self): return self._text
    def is_visible(self): return self._visible
    def input_value(self): return self._value


class _Scope:
    """A chatik frame: bubbles by the address hh gives message bodies, and
    the cover field by the cover-input cascade."""
    def __init__(self, bubbles, field=None):
        self._bubbles, self._field = bubbles, field
    def query_selector_all(self, selector):
        return list(self._bubbles) if selector == SELECTORS['chatik_bubble_text'] else []
    def query_selector(self, selector):
        return self._field if (self._field and selector in SELECTORS['chatik_cover_input']) else None


class _Page:
    def wait_for_timeout(self, ms): pass


h = ChatHandler.__new__(ChatHandler)
# A synthetic letter, never a real one: a fixture is published with the engine,
# and a sentence from someone's actual letter names their employer and their work.
letter = "В прошлой команде я вел платежный продукт от первой версии до запуска и отвечал за удержание."

# The letter's opening words in a bubble: seen, whatever hh did to the whitespace.
wrapped = _El("В прошлой команде я вел платежный продукт\nот первой версии до запуска и отвечал за удержание.")
with redirect_stdout(io.StringIO()):
    seen = h._letter_appeared(_Scope([_El("Отклик на вакансию"), wrapped]), letter, _Page(), timeout_ms=100)
check("the letter is confirmed by its own words in a bubble, across nbsp and re-wrapping", seen)

# Only hh's own bubble there: not seen, and the wait ends.
with redirect_stdout(io.StringIO()):
    seen = h._letter_appeared(_Scope([_El("Отклик на вакансию Без сопроводительного письма")]), letter, _Page(), timeout_ms=100)
check("hh's 'no cover letter' bubble is not the letter", not seen)
check("an empty letter is never confirmed",
      not h._letter_appeared(_Scope([_El("")]), "", _Page(), timeout_ms=50))

# The one state a second attempt is safe in: the field is on screen and empty.
check("a visible, empty cover field is the retry state",
      h._cover_field_is_empty(_Scope([], field=_El("", visible=True, value="")), None))
check("a field that still holds text is not",
      not h._cover_field_is_empty(_Scope([], field=_El("", visible=True, value="still here")), None))
check("a field that is gone is not",
      not h._cover_field_is_empty(_Scope([], field=None), _El("", visible=False)))

# Verification: positive evidence, or nothing typed — never "the input is empty".
h._cover_typed, h._cover_confirmed = True, True
check("a confirmed letter verifies without reading the input", h.verify_submission(object()) is True)
h._cover_typed, h._cover_confirmed = False, False
check("no letter typed is nothing to verify", h.verify_submission(object()) is True)

src = (_ENGINE / "adapters/hh/handlers/chat.py").read_text(encoding="utf-8")
_send = src.split("# 7. Send cover letter")[1].split("    # ── Private helpers")[0]
check("the send is confirmed by the letter appearing, not by the gesture",
      "confirmed = sent and self._letter_appeared(" in _send)
check("one more attempt only when the field is there and empty",
      "_cover_field_is_empty(" in _send and _send.count("self._type_cover(") == 1)
check("an unconfirmed letter is recorded as no cover, with the attempt on record",
      '"applied_via_chat_no_cover"' in _send and "'cover_unconfirmed': True" in _send
      and "never appeared in the chat" in _send)
check("the sent record is written only after confirmation",
      _send.index("self._cover_confirmed = bool(confirmed)") < _send.index('status="applied_via_chat",'))

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
