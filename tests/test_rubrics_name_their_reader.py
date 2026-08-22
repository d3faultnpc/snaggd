"""
Every section of a profile is addressed to someone, and says who.

The frame has always declared which sections exist. It never declared which call
has business reading any of them, and the consequence was that all of them went
into the system prompt of every call as one undivided block: the scorer read a
salary range and a relocation preference while deciding whether a person can do
a job, and a section nobody named — Tools — sat in every prompt influencing by
presence with no rule attached.

Nothing here projects anything yet. This checks the vocabulary is complete and
honest, which is what makes the projection reviewable rather than a rewrite.

The second profile file that used to sit beside candidate.md is gone as of
2026-08-21, so the checks that made its drift audible are gone with it: there is
one document now, and nothing to disagree with.

Run:  venv/bin/python3 tests/test_rubrics_name_their_reader.py
"""
import os
import sys
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
os.environ.setdefault("LLM_API_KEY", "test-key")

from onboarding.profile_frame import (  # noqa: E402
    DECLARED_SECTIONS, HEADING_KINDS, READER_KINDS, SECTION_READERS, readers_of,
)
from core.llm_agent import LLMAgent  # noqa: E402

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


# ── Every rubric has an address ──────────────────────────────────────────────
print("\nNo section is left without a reader:")

# Both vocabularies the frame keeps: the prose/key-value sections, and the
# evidence headings. The sin is OMISSION, not emptiness. A section missing from
# the map reaches every call with nothing said about what to do with it, because
# project_for keeps what it does not recognise. A section mapped to an empty tuple
# is the opposite: an explicit "addressed to nobody", and it reaches no one.
_every_section = set(DECLARED_SECTIONS) | set(HEADING_KINDS)
_unaddressed = sorted(s for s in _every_section
                      if s not in SECTION_READERS and s not in HEADING_KINDS)
check(f"every declared section is named in the map (missing: {_unaddressed or 'none'})",
      not _unaddressed)

# Career Profile is the one addressed to nobody today, and it has to be visibly
# deliberate rather than an oversight — the two look identical from a distance and
# behave oppositely.
check("a section addressed to nobody says so explicitly",
      SECTION_READERS.get("Career Profile") == ())

_unknown = sorted(s for s in SECTION_READERS if s not in _every_section)
check(f"and no reader is declared for a section the frame does not have "
      f"(unknown: {_unknown or 'none'})", not _unknown)

_bad_kinds = sorted({k for readers in SECTION_READERS.values()
                     for k in readers if k not in READER_KINDS})
check(f"every reader is a real kind of call (invented: {_bad_kinds or 'none'})",
      not _bad_kinds)


# ── The declaration has to mean something ────────────────────────────────────
print("\nThe map says something, rather than listing everyone everywhere:")

# If every section were readable by every call, the map would be decoration. The
# four below are the ones an employer's form asks about and capability does not
# depend on — they are the reason the split exists at all.
_answerer_only = ("Desired Salary", "Relocation & Work Format", "Identity")
for _section in _answerer_only:
    check(f"{_section} is for answering questions, not for scoring capability",
          readers_of(_section) == ("answer",))

# Languages left that list on 2026-08-22. It was there because the frame calls it
# the open-vocabulary section, not because a language is unrelated to capability —
# and for a translator, a salesperson or a hotel receptionist it IS the requirement.
check("languages reach the scorer, because a posting names them as a requirement",
      "score" in readers_of("Languages"))

check("evidence is read by everyone, because it is the person's own record",
      all(set(readers_of(h)) == {"score", "cover", "answer"} for h in HEADING_KINDS))
check("and that holds for every kind of evidence, not just employment",
      len(HEADING_KINDS) >= 8)


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
