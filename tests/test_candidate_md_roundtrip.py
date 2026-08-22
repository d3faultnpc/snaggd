"""
Opening the wizard on a profile and saving it back must change nothing.

Why this is the test (2026-08-14): the wizard prefilled from candidate.json, and
a profile existed whose candidate.json was an all-null skeleton next to a
candidate.md holding 104 lines of real content. The wizard opened empty over a
full profile — one click away from becoming the profile, which is how the JSON
had been emptied in the first place.

So the wizard reads the markdown now, and the property that keeps it honest is a
round trip through the real save path:

    to_md(parse_candidate_md(md), existing_content=md) == md

Not `to_md(parse(md), "")` — that fails by design and should. `to_md` renders its
own sections in its own order and knows nothing about `relocation_cities`,
`work_format_priority` or a `## Side Projects` heading someone wrote by hand.
Those survive because saving MERGES (onboarding/md_merge.py). Testing the
merge-free render would pin the wrong contract and quietly bless data loss.

Pure string work — no network, no LLM, no filesystem writes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Every shape to_md can emit, including the hand-written keys and headings it has
# never heard of. Modelled on a real profile.
PROFILE_MD = """# Fintech PM

## Career Profile
role_type: builder
hand_written_key: survives
edge: designs systems from inside the domain
aspiration: move deeper into agentic product work
not_looking_for: process_management, pmm, outsource

## Relocation & Work Format
relocation: yes
relocation_cities: city A (current), abroad (ok)
work_format: Hybrid

## Desired Salary
telecom: 100 000+ net
default: 120 000+ net
note: input/modal → number only

## Identity
name: Test Person
location: city A
email: someone@example.com
telegram: https://t.me/example

## Skills
- platform thinking
- product discovery

## Tools
Jira, Figma, SQL

## Languages
english: B2 (working proficiency)
russian: native

## Work Experience

### Example Corp | Product Manager | 2020 — 2026 | fintech
url: https://example.com

#### Zone of Responsibility
- roadmap
- discovery

#### Storefront MVP
Context: built from zero
- 30% conversion lift

## Education

### Some University | BSc | 2014 — 2018

## Certificates & Courses

### Coursera | Product Analytics | 2023

## Projects

### Side Thing | maintainer | 2024
Context: a weekend build that kept going
Stack: python, sqlite
- 400 stars

## Side Projects

### A Thing I Organise My Own Way
- still mine

## My Own Section
whatever a person wants to write here

## Additional
interests: Bitcoin Ordinals, UTXO data model
"""


def run():
    from onboarding.md_parse import merge_over_json, parse_candidate_md
    from onboarding.resume_parser import ResumeData, ResumeParser

    failures = []

    def check(label, condition):
        print(f"  {'✅' if condition else '❌'} {label}")
        if not condition:
            failures.append(label)

    parsed = parse_candidate_md(PROFILE_MD)

    # ── The fields the wizard's steps are made of ─────────────────────────
    check("Identity comes back", parsed["identity"]["name"] == "Test Person"
          and parsed["identity"]["location"] == "city A"
          and parsed["identity"]["role"] == "Fintech PM")
    check("contacts come back, unlabelled again",
          parsed["identity"]["contacts"] == ["someone@example.com", "https://t.me/example"])
    check("Career Profile comes back", parsed["career_profile"]["role_type"] == "builder")
    check("not_looking_for comes back as rules.penalize — the field the wizard shows",
          parsed["rules"]["penalize"] == ["process_management", "pmm", "outsource"])
    check("work_format comes back", parsed["logistics"]["work_format"] == "Hybrid")
    check("a multi-line salary comes back whole, keys and all",
          parsed["search"]["salary"].count("\n") == 2
          and "note: input/modal" in parsed["search"]["salary"])
    check("skills come back", parsed["skills"] == ["platform thinking", "product discovery"])
    check("tools come back", parsed["tools"] == ["Jira", "Figma", "SQL"])
    check("languages come back with their note",
          parsed["languages"][0] == {"lang": "english", "level": "B2", "note": "working proficiency"})
    check("interests come back", parsed["interests"] == ["Bitcoin Ordinals", "UTXO data model"])

    # ── Cases, including the parts that are easy to lose ──────────────────
    by_company = {c.get("company"): c for c in parsed["cases"]}
    check("every evidence section is read, declared or not", len(parsed["cases"]) == 5)
    work = by_company["Example Corp"]
    check("a work case keeps company/role/period/domain",
          work["role"] == "Product Manager" and work["period"] == "2020 — 2026"
          and work["domain"] == "fintech")
    check("its url survives", work["url"] == "https://example.com")
    check("Zone of Responsibility becomes responsibilities, not a highlight",
          work["responsibilities"] == ["roadmap", "discovery"])
    check("a highlight keeps its label, context and results",
          work["highlights"] == [{"label": "Storefront MVP", "context": "built from zero",
                                  "results": ["30% conversion lift"]}])
    check("education is typed from its heading", by_company["Some University"]["type"] == "education")
    check("a project is typed from its heading", by_company["Side Thing"]["type"] == "project")
    # The distinction three headings could not hold: a diploma and a requalification
    # certificate came back identical, because both had been rendered into one section.
    check("a credential is a kind of its own, not education and not a project",
          by_company["Coursera"]["type"] == "credential")
    # A heading nobody declared is captured as data rather than guessed at or dropped.
    own = by_company["A Thing I Organise My Own Way"]
    check("a hand-written heading becomes kind=other carrying its own text as the label",
          own["type"] == "other" and own["label"] == "Side Projects")
    check("prose lines the schema has no field for survive on the case",
          by_company["Side Thing"]["notes"] == {"Stack": "python, sqlite"})

    # ── The invariant ─────────────────────────────────────────────────────
    # A save converges the file on the frame's own vocabulary and then stays put.
    # Byte-identity on the FIRST save cannot be the test any more: a file holding a
    # heading nobody declared is not yet canonical, and the whole point is that it
    # stops being a heading. Idempotence is the honest form of the same guarantee —
    # and the one a person actually feels, since it says re-opening the wizard and
    # pressing save changes nothing at all.
    saved = ResumeParser(None).to_md(ResumeData(**parsed), existing_content=PROFILE_MD)
    again = ResumeParser(None).to_md(ResumeData(**parse_candidate_md(saved)), existing_content=saved)
    check("a saved profile saves again to the same bytes", again == saved)
    check("a heading nobody declared stops existing", "## My Own Section" not in saved)
    check("…and its text is kept, re-homed rather than dropped",
          "whatever a person wants to write here" in saved)
    check("a hand-organised EVIDENCE heading is not dissolved — it is placed by kind, "
          "and its items carry the heading as their own label",
          "## Side Projects" in saved)

    # ── One edit changes one thing ────────────────────────────────────────
    edited = dict(parsed)
    edited["rules"] = {"penalize": parsed["rules"]["penalize"] + ["project manager"]}
    saved = ResumeParser(None).to_md(ResumeData(**edited), existing_content=PROFILE_MD)
    check("adding one item to `Rather not` adds exactly one item",
          "not_looking_for: process_management, pmm, outsource, project manager" in saved)
    check("…and touches nothing else",
          "hand_written_key: survives" in saved and "relocation_cities: city A (current), abroad (ok)" in saved
          and "telecom: 100 000+ net" in saved)

    # A heading this renderer used to write, and no longer does, is read for its
    # contents and then dropped — otherwise the save leaves a stale second copy of
    # the same cases beside the new section. Only the frame's own retired headings.
    retired = PROFILE_MD.replace("## Projects\n", "## Projects & Credentials\n", 1)
    migrated = ResumeParser(None).to_md(ResumeData(**parse_candidate_md(retired)),
                                        existing_content=retired)
    check("a retired heading migrates instead of duplicating",
          "## Projects & Credentials" not in migrated and "## Projects" in migrated)
    check("…carrying its cases with it", "### Side Thing | maintainer | 2024" in migrated)
    check("…while a hand-organised evidence heading is left exactly where it was",
          "## Side Projects" in migrated)

    # ── Evidence is owned by kind ─────────────────────────────────────────
    # The live failure this pins: a CV stated the same two projects the file already
    # held under a hand-written heading, and they were written a second time instead
    # of replacing the first — leaving the profile asserting two versions of itself.
    cv = {"identity": {"name": "Test Person", "role": "Fintech PM"},
          "cases": [{"type": "employment", "company": "New Corp", "role": "PM", "period": "2026"},
                    {"type": "project", "company": "Only Project From CV"}],
          "skills": ["SQL"]}
    saved = ResumeParser(None).to_md(ResumeData(**cv), existing_content=PROFILE_MD)
    check("a CV that states projects replaces them — one list, not two",
          "Only Project From CV" in saved and "Side Thing" not in saved)
    check("…and the same for employment", "New Corp" in saved and "Example Corp" not in saved)
    check("a kind the CV said nothing about is left alone",
          "Some University" in saved and "Coursera" in saved)
    check("unclassified residue does not survive a save that carries evidence — "
          "no CV ever emits `other`, so anything left there would be trapped for good",
          "A Thing I Organise My Own Way" not in saved)
    check("preferences the CV knows nothing about are untouched as always",
          "not_looking_for: process_management, pmm, outsource" in saved
          and "telecom: 100 000+ net" in saved)

    # A save carrying no evidence at all is not a statement about evidence.
    nothing = ResumeParser(None).to_md(ResumeData(identity={"name": "Test Person"}),
                                       existing_content=PROFILE_MD)
    check("a save with no evidence changes no evidence",
          "Example Corp" in nothing and "Side Thing" in nothing
          and "A Thing I Organise My Own Way" in nothing)

    # ── The JSON only fills what the markdown cannot carry ────────────────
    merged = merge_over_json(PROFILE_MD, {
        "schema_version": "1.0", "target_market": "cis",
        "identity": {"name": "Stale Name", "location": "Stale City"},
        "skills": [], "cases": [],
    })
    check("markdown wins over a stale JSON field", merged["identity"]["name"] == "Test Person")
    check("an emptied JSON list does not win over real markdown content", len(merged["skills"]) == 2)
    check("JSON-only fields are kept", merged["target_market"] == "cis")

    # ── An entry with nothing to name it still round-trips ────────────────
    # The heading is a boundary md_parse reads; its text is content. When a case
    # carries no company, role, period or domain, the heading is bare — it used
    # to print "MISSING — company/role/period", and candidate.md goes into the
    # system prompt verbatim, so an instruction meant for a person was read by
    # the model as a fact about the candidate.
    unnamed = ResumeParser(None).to_md(ResumeData(
        identity={"name": "Test Person"},
        cases=[{"type": "project",
                "highlights": [{"label": None, "context": "Did a thing.", "results": []}]}],
    ))
    check("an unnamed entry renders a bare heading, not a note to the reader",
          "###" in unnamed and "MISSING" not in unnamed)
    _again = ResumeParser(None).to_md(
        ResumeData(**parse_candidate_md(unnamed)), existing_content=unnamed)
    check("and it survives a parse and a re-save unchanged", _again == unnamed)

    # ── A group of bullets is bounded whether or not it has a name ─────────
    # Until 2026-08-17 the boundary WAS the name: `#### {label}` was written only
    # when the model had invented one, so two unnamed groups in one entry came
    # back as a single merged group (2 in, 1 out — reproduced deterministically,
    # no model involved). The same line also made a model's phrase a heading in
    # a person's own file. Split in two: `####` is the boundary and says nothing;
    # `label:` is the name, written as content beside `Context:` and `url:`.
    print("\nGroups of bullets inside one entry:")

    def _groups(cases):
        md = ResumeParser(None).to_md(ResumeData(
            identity={"name": "Test Person"}, cases=cases))
        back = parse_candidate_md(md)["cases"][0]
        return md, (back.get("highlights") or [])

    _two = [{"type": "employment", "company": "Example Corp", "role": "PM",
             "highlights": [{"results": ["a", "b"]}, {"results": ["c"]}]}]
    _md, _hl = _groups(_two)
    check("two unnamed groups come back as two, not as one", len(_hl) == 2)
    check("and the second one's bullet did not join the first",
          _hl[0]["results"] == ["a", "b"] and _hl[1]["results"] == ["c"])

    _one = [{"type": "employment", "company": "Example Corp", "role": "PM",
             "highlights": [{"results": ["a", "b"]}]}]
    _md, _hl = _groups(_one)
    check("a single unnamed group needs no boundary line at all", "####" not in _md)
    check("and still reads back as one group", len(_hl) == 1)

    _named = [{"type": "employment", "company": "Example Corp", "role": "PM",
               "highlights": [{"label": "Storefront MVP", "results": ["a"]}]}]
    _md, _hl = _groups(_named)
    check("a group's name is content, not a heading",
          "#### Storefront MVP" not in _md and "label: Storefront MVP" in _md)
    check("and it comes back attached to its group",
          len(_hl) == 1 and _hl[0].get("label") == "Storefront MVP")
    check("the only text ever written after #### is the frame's own",
          all(l.strip() in ("####", "#### Zone of Responsibility")
              for l in _md.splitlines() if l.startswith("####")))

    # A file saved before the split still reads, and one save converts it —
    # read tolerantly, write strictly, so this stays one legacy form rather
    # than a table of accepted shapes.
    _legacy = ("# PM\n\n## Identity\nname: Test Person\n\n## Work Experience\n\n"
               "### Example Corp | PM\n\n#### Storefront MVP\n- a\n\n"
               "#### Zone of Responsibility\n- own the roadmap\n")
    _read = parse_candidate_md(_legacy)["cases"][0]
    check("the old shape still names its group",
          (_read.get("highlights") or [{}])[0].get("label") == "Storefront MVP")
    check("and the frame's own heading still means responsibilities",
          _read.get("responsibilities") == ["own the roadmap"])
    _converted = ResumeParser(None).to_md(
        ResumeData(**parse_candidate_md(_legacy)), existing_content=_legacy)
    check("one save rewrites it into the new shape",
          "#### Storefront MVP" not in _converted and "label: Storefront MVP" in _converted)
    check("without losing anything on the way",
          (parse_candidate_md(_converted)["cases"][0].get("highlights") or [{}])[0]
          .get("label") == "Storefront MVP")

    # ── Skills is read in both shapes it exists in ────────────────────────
    # `to_md` writes skills as bullets and tools as a `tools:` line — the same
    # kind of list, two shapes, adjacent in the file. A person editing Skills by
    # hand writes what Tools taught them, and until 2026-08-17 `skills: a, b`
    # matched nothing here: the section parsed to an empty list and the profile
    # lost every skill it had, with no error anywhere.
    print("\nSkills, however it was written:")
    _bulleted = "# X\n\n## Identity\nname: N\n\n## Skills\n- python\n- sql\n"
    _keyed_md = "# X\n\n## Identity\nname: N\n\n## Skills\nskills: python, sql\n"
    _bare_md = "# X\n\n## Identity\nname: N\n\n## Skills\npython, sql\n"
    check("bullets, as the wizard writes them",
          parse_candidate_md(_bulleted).get("skills") == ["python", "sql"])
    check("a `skills:` line, as someone copying the Tools section writes it",
          parse_candidate_md(_keyed_md).get("skills") == ["python", "sql"])
    check("and a bare list, as older files hold it",
          parse_candidate_md(_bare_md).get("skills") == ["python", "sql"])

    # ── A market convention may drop a heading, never the bullets under it ────
    # Until 2026-08-22 a profile reading as western or global lost every
    # responsibility bullet on save. "Zone of Responsibility" is a CIS employment
    # convention; the bullets are what a person typed into the wizard, which writes
    # into that field for a card of any kind. All 13 live profiles read as `cis`, so
    # this never fired — it was waiting for the first international CV.
    print("\nA market convention drops a heading, not content:")
    western = ResumeData(
        identity={"name": "W"}, target_market="western",
        cases=[{"type": "employment", "company": "Acme", "role": "PM",
                "responsibilities": ["owned the roadmap", "ran weekly reviews"]}])
    western_md = ResumeParser(None).to_md(western)
    check("a western profile keeps its responsibility bullets",
          "owned the roadmap" in western_md and "ran weekly reviews" in western_md)
    check("and drops only the CIS heading",
          "Zone of Responsibility" not in western_md)
    # They come back under the entry as an unnamed group rather than as
    # `responsibilities`, because the heading is what carried that meaning. Stated as
    # the property it is, not the one that would be nicer: the same already happens to
    # an award's or a degree's bullets, since the heading is written for employment
    # only. What must never happen is the bullets disappearing, which is what this
    # file exists to catch.
    western_case = parse_candidate_md(western_md)["cases"][0]
    survivors = set(western_case.get("responsibilities") or [])
    for group in (western_case.get("highlights") or []):
        survivors |= set(group.get("results") or [])
    check("and every bullet survives the round trip, under the same case",
          survivors == {"owned the roadmap", "ran weekly reviews"})

    cis = ResumeData(
        identity={"name": "C"}, target_market="cis",
        cases=[{"type": "employment", "company": "Acme", "role": "PM",
                "responsibilities": ["owned the roadmap"]}])
    check("a CIS employment entry still gets the heading",
          "Zone of Responsibility" in ResumeParser(None).to_md(cis))

    print()
    print(f"{'❌ ' + str(len(failures)) + ' failed' if failures else '✅ all passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
