"""A contact line wears exactly one label, and it is ours.

Found on a live parse, 2026-08-16: candidate.md came back holding
`telegram: Telegram: https://t.me/...` and a bare `Email: ...`. Two symptoms of
one cause — `_typed_contact_line` sniffed the raw string and prepended a label
without ever asking whether the string already carried one. The email branch was
missed for a second reason worth naming: it requires the value to hold no space,
and the space after the source's own colon is a space.

The second symptom is the expensive one. `Email:` is a key, and not one the
frame owns, so `profile_frame.section_for_key` files it under Languages (the
open-vocabulary section) and the next save moves the person's email address into
their spoken languages. `md_parse`, reading the same file, counts the line as
prose and folds it into the pitch. Neither shows up as an error anywhere — the
file just quietly stops meaning what it says.

Run:  python3 tests/test_contact_lines_carry_one_label.py
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from onboarding.profile_frame import KEY_OWNERS, section_for_key  # noqa: E402
from onboarding.resume_parser import _typed_contact_line  # noqa: E402

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


# raw as the model wrote it → the key we must produce, and the value under it
LABELLED = [
    ("Telegram: https://t.me/example", "telegram", "https://t.me/example"),
    ("telegram: @example",             "telegram", "@example"),
    ("Email: person@example.com",      "email",    "person@example.com"),
    ("E-mail: person@example.com",     "email",    "person@example.com"),
    ("GitHub: github.com/example",     "github",   "https://github.com/example"),
    ("LinkedIn — linkedin.com/in/ex",  "linkedin", "https://linkedin.com/in/ex"),
    ("Phone: +7 900 000 00 00",        "phone",    "+7 900 000 00 00"),
]

print("A label the source already wrote does not survive next to ours:")
for _raw, _key, _value in LABELLED:
    _line = _typed_contact_line(_raw)
    check(f"{_raw!r} → {_key}: …", _line == f"{_key}: {_value}")

print("\nAnd a bare value still gets one:")
check("t.me/example → telegram, with a scheme",
      _typed_contact_line("t.me/example") == "telegram: https://t.me/example")
check("person@example.com → email",
      _typed_contact_line("person@example.com") == "email: person@example.com")
check("two labels on one line both come off",
      _typed_contact_line("Contact: Telegram: @example") == "telegram: @example")

print("\nA URI scheme is a colon too, and is not a label:")
check("https:// survives being read for a label",
      _typed_contact_line("https://t.me/example") == "telegram: https://t.me/example")
check("and so does a slashless one, inside the value rather than as its key",
      _typed_contact_line("mailto:person@example.com") == "email: mailto:person@example.com")

print("\nEvery key this function writes is a key the frame owns:")
for _raw, _key, _ in LABELLED:
    check(f"{_key} is in the frame's vocabulary, and lands in Identity",
          _key in KEY_OWNERS and section_for_key(_key) == "Identity")

print("\nWhat cannot be typed is returned without inventing a key:")
# md_parse documents this function as passing an unrecognised contact through
# unlabeled — a single bare token reads back as a contact, anything else as the
# pitch. Emitting `Skype: x` instead invents a key nobody owns, which is the
# whole failure above wearing a different word.
_untyped = _typed_contact_line("Skype: example.handle")
check("the label comes off rather than becoming a key", _untyped == "example.handle")
check("so the frame is never handed a key it does not own",
      ":" not in _untyped and section_for_key("Skype") == "Languages")

print()
_total, _passed = len(results), sum(results)
print(f"{_passed}/{_total} passed")
sys.exit(0 if _passed == _total else 1)
