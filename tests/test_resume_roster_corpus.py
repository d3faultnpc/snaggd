"""The roster read against every real hh page this machine has captured.

Why a corpus and not more synthetic fixtures: the claims pinned here are claims
about hh, not about our parsing — "the state store is on every vacancy page",
"the node holds exactly one vacancy", "a page can carry the node and no
`resumes` key at all". A fixture can only restate what we already believed. The
third of those was found by reading 110 real captures, and it is the difference
between an empty roster and a crash.

It also covers the case this account can no longer produce by hand: exactly ONE
resume. Most of the corpus predates the second one, so that path stays testable
after the account moved past it — which is the whole reason the corpus is worth
reading instead of deleting.

The corpus lives in debug_screenshots/, gitignored, holding real postings. This
skips wherever it is absent, asserts only on shape and counts, and prints no
title, employer or vacancy id.

Run:  python3 tests/test_resume_roster_corpus.py
"""
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.hh.resume_roster import resume_roster

_CORPUS = Path(__file__).parent.parent / "debug_screenshots"
_TEMPLATE = re.compile(
    r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>', re.S)


def _state(path):
    m = _TEMPLATE.search(path.read_text(encoding="utf-8", errors="ignore"))
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except Exception:
        return None


def run():
    failures = []

    def check(label, condition):
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")
        if not condition:
            failures.append(label)

    captures = sorted(_CORPUS.glob("*/*/01_vacancy_page.html")) if _CORPUS.is_dir() else []
    if not captures:
        print("  SKIP  no local capture corpus (debug_screenshots/ is gitignored)")
        return 0

    print(f"  corpus: {len(captures)} captured vacancy pages\n")

    states = [(p, _state(p)) for p in captures]
    check("every captured page carries a readable state store",
          all(s is not None for _, s in states))

    nodes = [(s or {}).get("applicantVacancyResponseStatuses") or {} for _, s in states]
    check("every captured page carries the response-status node",
          all(bool(n) for n in nodes))

    # Addressed by vacancy id, with "the only one there" as the fallback — a
    # second entry would turn that fallback into a guess.
    check("the node holds exactly one vacancy",
          all(len(n) == 1 for n in nodes))

    rosters = []
    raised = 0
    for (p, s), n in zip(states, nodes):
        vid = next(iter(n), None)
        try:
            rosters.append(resume_roster(s or {}, vid))
        except Exception:
            raised += 1
            rosters.append(None)
    check("reading the roster never raises, whatever the node holds", raised == 0)

    # 2 of the 110 carry the node with no `resumes` key. Absence is a slot, not
    # an answer: empty roster, and the caller refuses rather than guesses.
    empty = sum(1 for r in rosters if r == [])
    check("a node without `resumes` yields an empty roster, not an error",
          empty > 0 and empty < len(rosters))

    entries = [e for r in rosters if r for e in r]
    check("every offered resume carries a hash and an id",
          entries and all(e["hash"] and e["id"] for e in entries))
    check("hash and id are different identifiers, never interchangeable",
          all(e["hash"] != e["id"] for e in entries))

    sizes = [len(r) for r in rosters if r is not None]
    check("the corpus still covers a one-resume account", sizes.count(1) > 0)
    check("the corpus still covers a multi-resume account", max(sizes) > 1)

    print()
    if failures:
        print(f"{len(failures)} failed")
        for f in failures:
            print(f"   · {f}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
