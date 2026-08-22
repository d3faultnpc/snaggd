"""
A call reads the part of the profile it is entitled to, and no more.

Every call used to receive the whole file. That is how the scorer came to hold a
salary range and a relocation preference while deciding whether a person can do
a job — sitting in the context, influencing by presence, with no rule attached.
The map that says who reads what already exists (SECTION_READERS); this is it
being enforced.

Off by default. It is the first change that alters what the model sees, so
turning it on is a decision with a measurement behind it.

Run:  venv/bin/python3 tests/test_profile_is_projected_per_call.py
"""
import os
import sys
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
os.environ.setdefault("LLM_API_KEY", "test-key")

from onboarding.profile_frame import HEADING_KINDS, project_for  # noqa: E402
from core.llm_agent import LLMAgent  # noqa: E402

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


_PROFILE = """# A person, in one line

## Career Profile
role_type: builder
not_looking_for: not this kind of work

## Skills
skills: one, two

## Desired Salary
default: a number

## Relocation & Work Format
relocation: yes

## Languages
english: C1

## Identity
name: A Person

## Work Experience

### A Company | A Role | 2020-2024
- did a thing

#### Zone of Responsibility
- and another thing

## Something The Frame Has Never Heard Of
a person's own heading
"""


def _headings(md):
    return [l[3:].strip() for l in md.splitlines() if l.startswith("## ")]


# ── Each slice is addressed ──────────────────────────────────────────────────
print("\nWhat each kind of call is entitled to:")

_score = project_for(_PROFILE, "score")
check("the scorer does not read a salary — it is not evidence of capability",
      "Desired Salary" not in _headings(_score))
check("nor a relocation preference", "Relocation & Work Format" not in _headings(_score))
check("nor personal contact details", "Identity" not in _headings(_score))
check("but it DOES read languages — a language is a requirement a posting states "
      "by name, and for a translator or a receptionist it is the requirement. "
      "Withholding it was this map's sharpest defect: 11 of 13 live profiles "
      "carry languages and the scoring prompt named them zero times",
      "Languages" in _headings(_score))
check("and it no longer reads what the person wants — role_type, edge and "
      "aspiration are out of scope while the scorer is rebuilt on evidence",
      "Career Profile" not in _headings(_score))

_answer = project_for(_PROFILE, "answer")
for _needed in ("Desired Salary", "Relocation & Work Format", "Languages", "Identity"):
    check(f"the answerer does read {_needed} — an employer asks about it unannounced",
          _needed in _headings(_answer))
check("and skills, because 'list your key skills' is an ordinary question",
      "Skills" in _headings(_answer))
check("but not the refusals — a form answer to an employer is not where those go",
      "not_looking_for" not in _answer)


# ── Evidence belongs to everyone ─────────────────────────────────────────────
print("\nEvidence is the person's own record:")
for _reader in ("score", "cover", "answer"):
    _slice = project_for(_PROFILE, _reader)
    check(f"{_reader} keeps Work Experience", "Work Experience" in _headings(_slice))
check("every kind of evidence is addressed, not just employment",
      all("score" in (lambda h: __import__("onboarding.profile_frame", fromlist=["readers_of"]).readers_of(h))(h)
          for h in HEADING_KINDS))


# ── It cuts where the frame cuts, and nowhere else ───────────────────────────
print("\nThe cut is at section level, and the text survives it:")
check("a case and the person's own #### under it stay with their section",
      "#### Zone of Responsibility" in _score and "- and another thing" in _score)
check("the headline above the first section always stays — it is who they are, "
      "not a rubric", _score.startswith("# A person, in one line"))
check("a heading the frame has never heard of is kept for everyone, because not "
      "recognising it is not the same as knowing it belongs elsewhere",
      all("Something The Frame Has Never Heard Of" in _headings(project_for(_PROFILE, r))
          for r in ("score", "cover", "answer")))
check("kept sections are passed through verbatim, not reformatted",
      "skills: one, two" in project_for(_PROFILE, "score"))


# ── The switch, and the cache behind it ──────────────────────────────────────
print("\nOff by default, and each slice cached as its own:")
import tempfile  # noqa: E402

_d = Path(tempfile.mkdtemp())
(_d / "candidate.md").write_text(_PROFILE, encoding="utf-8")

os.environ.pop("SNAGGD_PROJECT_PROFILE", None)
with redirect_stdout(StringIO()):
    _agent = LLMAgent(data_dir=_d)
    _a, _b = _agent._system("score"), _agent._system("answer_question")
check("with the switch off the answerer still reads the whole file",
      "Desired Salary" in _b and "Career Profile" in _b)
check("but the scorer projects either way — it is not an option there, and a "
      "scorer measured with the whole profile is not the scorer that ships",
      _a != _b and "Desired Salary" not in _a)

os.environ["SNAGGD_PROJECT_PROFILE"] = "1"
with redirect_stdout(StringIO()):
    _agent = LLMAgent(data_dir=_d)
    _s, _ans = _agent._system("score"), _agent._system("answer_question")
    _s_again = _agent._system("score")
check("with it on the scorer and the answerer get different slices", _s != _ans)
check("and asking twice returns the same slice, not the other one's",
      _s_again == _s and "Desired Salary" not in _s_again)
check("a call type nobody mapped still gets the whole file rather than nothing",
      "Desired Salary" in _agent._system("modal_action"))
os.environ.pop("SNAGGD_PROJECT_PROFILE", None)


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
