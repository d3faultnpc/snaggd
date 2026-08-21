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

The second half is about a different kind of silence. A profile built by the CLI
wizard carries job_preferences.md as well, and on a live profile the two files
disagreed: a salary range in one and "Not specified — open to market rate" in the
other, both in the same system prompt. The frame's own comment on stop_categories
already says why that is wrong — one line the model and the validator both read,
"instead of two copies in two files drifting apart". This makes the drift audible.

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
# evidence headings. A section in either one with no reader is a rubric that
# reaches the model with nothing said about what to do with it.
_every_section = set(DECLARED_SECTIONS) | set(HEADING_KINDS)
_unaddressed = sorted(s for s in _every_section if not readers_of(s))
check(f"every declared section names who reads it (unaddressed: {_unaddressed or 'none'})",
      not _unaddressed)

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
_answerer_only = ("Desired Salary", "Relocation & Work Format", "Languages", "Identity")
for _section in _answerer_only:
    check(f"{_section} is for answering questions, not for scoring capability",
          readers_of(_section) == ("answer",))

check("evidence is read by everyone, because it is the person's own record",
      all(set(readers_of(h)) == {"score", "cover", "answer"} for h in HEADING_KINDS))
check("and that holds for every kind of evidence, not just employment",
      len(HEADING_KINDS) >= 8)


# ── A second profile file is announced, not absorbed ─────────────────────────
print("\nA second file describing the same person is said out loud:")


def _agent_over(files: dict) -> tuple:
    import tempfile
    d = Path(tempfile.mkdtemp())
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")
    agent = LLMAgent(data_dir=d)
    buf = StringIO()
    with redirect_stdout(buf):
        agent._system()
    return agent, buf.getvalue()

_only_one = {"candidate.md": "# person\n\n## Desired Salary\n100\n"}
_agent, _out = _agent_over(_only_one)
check("one profile file, nothing to warn about", "restates" not in _out)

_two = dict(_only_one)
_two["job_preferences.md"] = "# prefs\n\n## Salary\nNot specified.\n"
_agent, _out = _agent_over(_two)
check("a heading the frame already owns under a shorter name is caught",
      "restates" in _out and "desired salary" in _out)

_two["job_preferences.md"] = "# prefs\n\nstop_categories:\n  - example\n"
_agent, _out = _agent_over(_two)
check("so is a key the frame assigns to candidate.md",
      "restates" in _out and "stop_categories" in _out)

_two["job_preferences.md"] = "# prefs\n\n## Target roles\n- Product Manager\n"
_agent, _out = _agent_over(_two)
check("but content the frame does not own is not flagged — this is about drift, "
      "not about the file existing", "restates" not in _out)

# The point of the warning is that both halves still reach the model: which of
# two contradicting documents wins is not a decision to make on someone's behalf.
_two["job_preferences.md"] = "# prefs\n\n## Salary\nNot specified.\n"
_agent, _ = _agent_over(_two)
check("and both files still go to the model — nothing was silently dropped",
      "Not specified" in _agent._system() and "## Desired Salary" in _agent._system())


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
