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

# Constraints is the one addressed to nobody, and it has to be visibly deliberate
# rather than an oversight — the two look identical from a distance and behave
# oppositely. (It was `Career Profile` until 2026-08-25, which held five keys of four
# natures; the three that were "what I want to be" are retired, and what was left
# split by readership into Constraints and Preferences.)
#
# Nobody, and not by accident: the scorer is handed the list explicitly by llm_agent,
# because a vocabulary an answer is validated against is data the call needs rather
# than a side effect of what projection happened to keep.
check("a section addressed to nobody says so explicitly",
      SECTION_READERS.get("Constraints") == ())

_unknown = sorted(s for s in SECTION_READERS if s not in _every_section)
check(f"and no reader is declared for a section the frame does not have "
      f"(unknown: {_unknown or 'none'})", not _unknown)

_bad_kinds = sorted({k for readers in SECTION_READERS.values()
                     for k in readers if k not in READER_KINDS})
check(f"every reader is a real kind of call (invented: {_bad_kinds or 'none'})",
      not _bad_kinds)


# ── The declaration has to mean something ────────────────────────────────────
print("\nThe map says something, rather than listing everyone everywhere:")

# If every section were readable by every call, the map would be decoration. Since
# 2026-08-25 the map has TWO readerships rather than three (the user's rule: the
# letter writer and the form answerer see everything declared; the scorer sees hard
# facts and, explicitly, not the salary). So what the map has to say is no longer
# "one section per caller" — it is which sections the SCORER is kept away from.
#
# These are the ones an employer asks about and capability does not depend on. They
# are the reason the projection exists at all: judging whether a person can do a job
# while holding their salary range and relocation preference is the coupling the
# scoring rebuild removed.
_not_for_scoring = ("Desired Salary", "Relocation & Work Format", "Identity", "Additional")
for _section in _not_for_scoring:
    check(f"{_section} is kept away from scoring capability",
          "score" not in readers_of(_section))
    # And the other half of the same statement: withheld from the scorer is not the
    # same as withheld from everyone. The letter writer was not being given the
    # NAME of the person it writes for, and nobody had noticed, because the flag
    # that enforces this map is off.
    check(f"...and still reaches the calls that write to an employer",
          set(readers_of(_section)) == {"cover", "answer"})

# The two exceptions, and they are exceptions for one reason: what a person refuses
# is not something to volunteer to an employer.
check("a hard refusal is addressed to nobody", readers_of("Constraints") == ())
# Corrected the same day it was written. The first version gave this to `cover`
# alone, splitting by CALL TYPE — but `fill_form` is one call that answers short
# fields AND writes the cover letter when a form has one glued among them, so a
# standalone letter avoided unwanted work and the same letter inside a form did not.
# What to WRITE is a prompt rule (prompts/form_fill.md carries it); a projection can
# only decide what is KNOWN.
check("a soft one reaches everything that writes to an employer",
      set(readers_of("Preferences")) == {"cover", "answer"})

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
