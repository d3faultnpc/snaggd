"""Saving a profile canonicalises its shape, and loses none of its content.

test_candidate_md_roundtrip.py pins the contract on one synthetic profile, which
is the right unit for the contract itself. This one runs the same save over the
profiles that actually exist on the machine, because the shapes that break a
canonicaliser are the ones a real parse produced months apart, not the ones a
fixture author thought to write down.

Three properties, and the third is the one worth the file:

  1. `#### <text>` does not survive a save. Since 2026-08-17 `####` is a boundary
     between bullet groups inside one entry and carries no text of its own; the
     group's name lives on a `label:` line under it. A file written before that
     holds the old shape, and the save is what is supposed to convert it.
  2. `## Skills` holds a `skills:` line, never a bullet list — the same rule
     `## Tools` has always followed.
  3. Neither conversion loses anything: the number of skills is the same before
     and after, and so is the number of bullets in every entry.

Property 3 exists because 1 and 2 are trivially satisfiable by deleting the
content, and a canonicaliser that drops what it cannot place would pass both.

`data/` is gitignored, so there is no corpus in a clean checkout. That is not a
skip-and-forget: the synthetic profile below is exercised unconditionally and
carries every shape the corpus was found to hold, so the file still tests
something on a machine that has no profiles. The corpus sweep adds population,
not coverage.

No network, no LLM, no writes — every profile is read, converted in memory and
thrown away.

Run:  python3 tests/test_canonical_form_over_corpus.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Every shape the local corpus was found to hold on 2026-08-18, with none of its
# content: an old-form `#### <text>` group, a bulleted `## Skills`, a `####`
# already in the new form, and the legacy `#### Zone of Responsibility` literal
# that is deliberately left alone.
LEGACY_MD = """# Some Role

## Identity
name: Test Person
location: city A
email: someone@example.com
https://example.net/profile/handle

## Skills
- alpha
- beta
- gamma

## Tools
Jira, Figma

## Work Experience

### Example Corp | Some Role | 2020 — 2026 | fintech

#### Zone of Responsibility
- first duty
- second duty

#### Storefront MVP
Context: built from zero
- one outcome
- another outcome

####
label: Already Converted
- a third outcome

## Education

### Some University | BSc | 2014 — 2018
"""

BULLET_RE = re.compile(r"^[-*] ", re.M)
OLD_HEADING_RE = re.compile(r"^#### +\S", re.M)
# The one old-form heading that is not converted on purpose — a legacy literal
# kept for the CIS resume convention (decision recorded 2026-08-17). It has to be
# excluded explicitly rather than by a general rule, which is the whole reason it
# is worth a named constant here.
KEPT_LITERAL = "#### Zone of Responsibility"


def _old_headings(md: str) -> list:
    return [h for h in OLD_HEADING_RE.findall(md) or []
            for h in [h]] and [line for line in md.splitlines()
                               if line.startswith("#### ") and line.strip() != KEPT_LITERAL]


def _skills_block(md: str) -> list:
    """Lines under `## Skills`, up to the next `##`."""
    out, inside = [], False
    for line in md.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.strip() == "## Skills"
            continue
        if inside:
            out.append(line)
    return out


def _skill_count(md: str) -> int:
    block = _skills_block(md)
    named = [l for l in block if l.startswith("skills:")]
    if named:
        value = named[0].split(":", 1)[1]
        return len([s for s in (p.strip() for p in value.split(",")) if s])
    return len([l for l in block if l.startswith("- ") or l.startswith("* ")])


def _bullet_count(md: str) -> int:
    return len(BULLET_RE.findall(md))


def _bullets_outside_skills(md: str) -> int:
    """Bullets everywhere except under `## Skills`.

    The skills bullets are supposed to stop being bullets, so counting them here
    would assert the opposite of the rule. Everything else must survive exactly.
    """
    inside, n = False, 0
    for line in md.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Skills"
            continue
        if not inside and (line.startswith("- ") or line.startswith("* ")):
            n += 1
    return n


def _save(md: str) -> str:
    from onboarding.md_parse import parse_candidate_md
    from onboarding.resume_parser import ResumeData, ResumeParser
    return ResumeParser(None).to_md(ResumeData(**parse_candidate_md(md)), existing_content=md)


def run():
    failures = []

    def check(label, condition):
        print(f"  {'✅' if condition else '❌'} {label}")
        if not condition:
            failures.append(label)

    # ── The declared shapes, on a profile that holds all of them ──────────
    print("\nsynthetic profile — every shape the corpus holds")
    saved = _save(LEGACY_MD)

    check("`#### <text>` does not survive a save",
          not [l for l in saved.splitlines()
               if l.startswith("#### ") and l.strip() != KEPT_LITERAL])
    check("…and the group's name is kept, on a `label:` line",
          "label: Storefront MVP" in saved)
    check(f"…while `{KEPT_LITERAL}` is left alone, as decided",
          KEPT_LITERAL in saved)
    check("`## Skills` holds one `skills:` line, not bullets",
          any(l.startswith("skills:") for l in _skills_block(saved))
          and not any(l.startswith("- ") for l in _skills_block(saved)))
    check("no skill is lost converting the list to a line",
          _skill_count(saved) == _skill_count(LEGACY_MD) == 3)
    check("no bullet is lost anywhere else in the file",
          _bullets_outside_skills(saved) == _bullets_outside_skills(LEGACY_MD) == 5)
    check("saving again changes nothing", _save(saved) == saved)

    # ── A contact the type-sniffer does not recognise ─────────────────────
    # `_typed_contact_line` passes an unknown contact through unlabelled by
    # design, and `md_parse` reads a bare single token back as a contact. The
    # risk is not that it is unlabelled — it is that a URL carries a colon, so
    # anything that splits the line on the first one reads `https` as its key.
    # This pins the round trip; how it is displayed is the caller's problem and
    # is checked in the frontend's own suite.
    from onboarding.md_parse import parse_candidate_md
    contacts = parse_candidate_md(saved)["identity"]["contacts"]
    check("an unrecognised contact survives a save whole, colon and all",
          "https://example.net/profile/handle" in contacts)
    check("…and is not split into a bogus `https` key",
          not any(c.startswith("//") for c in contacts)
          and "https" not in parse_candidate_md(saved)["identity"])

    # ── The name the switcher shows ───────────────────────────────────────
    # The profile switcher and the sidebar show `candidate_headline`, which is
    # NOT line 1: `_extract_headline` skips blanks and the `<!-- snaggd:start -->`
    # marker that to_md() writes ahead of the title. That skip is the fix for a
    # 2026-07-15 bug where every profile was labelled with the marker text, and
    # it is pinned here because reading line 1 directly still looks correct on
    # eleven of twelve profiles and wrong on the twelfth.
    from api import _extract_headline
    check("the switcher shows a title, not the managed-block marker",
          _extract_headline(saved) and not _extract_headline(saved).startswith("<!--"))
    check("…even when the marker is literally line 1",
          _extract_headline("<!-- snaggd:start -->\n# Some Role\n") == "Some Role")

    # ── Population: every profile on this machine, if there are any ───────
    corpus = sorted((Path(__file__).parent.parent / "data" / "profiles").glob("*/candidate.md"))
    if not corpus:
        print("\nno local corpus (data/ is gitignored) — synthetic profile only")
    else:
        print(f"\nlocal corpus — {len(corpus)} profiles (reported by index, never by name)")
        for i, path in enumerate(corpus):
            md = path.read_text(encoding="utf-8")
            try:
                out = _save(md)
            except Exception as e:
                check(f"profile #{i} saves at all ({type(e).__name__})", False)
                continue
            check(f"profile #{i}: no `#### <text>` survives",
                  not [l for l in out.splitlines()
                       if l.startswith("#### ") and l.strip() != KEPT_LITERAL])
            check(f"profile #{i}: `## Skills` is a line, not a list",
                  not any(l.startswith("- ") for l in _skills_block(out)))
            # `>=`, not `==`, and the reason is the rule itself: a bullet holding
            # a hand-written comma list is READ tolerantly and WRITTEN strictly,
            # so it legitimately becomes several skills. Only a decrease is a bug.
            check(f"profile #{i}: loses no skill ({_skill_count(md)} → {_skill_count(out)})",
                  _skill_count(out) >= _skill_count(md))
            check(f"profile #{i}: keeps every bullet outside Skills "
                  f"({_bullets_outside_skills(md)})",
                  _bullets_outside_skills(out) == _bullets_outside_skills(md))
            headline = _extract_headline(md)
            check(f"profile #{i}: the switcher shows a title, not markup",
                  bool(headline) and not headline.startswith("<!--")
                  and not headline.startswith("- "))
            check(f"profile #{i}: saving again changes nothing", _save(out) == out)

    print()
    if failures:
        print(f"❌ {len(failures)} failed")
        for f in failures:
            print(f"   · {f}")
        return 1
    print("✅ all passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
