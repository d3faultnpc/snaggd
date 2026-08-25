"""
Data put into a profile must come back out of it. That is the whole contract.

Why this test exists (2026-08-26). The frame declared the AXES — sections, kinds,
key ownership, readership — and those held for five sprints. What it never
declared was the shape INSIDE a record, so `to_md` wrote one and `md_parse` read
another and nothing checked that they agreed. Every defect found that day lived
in that gap.

The existing round-trip test asks `to_md(parse(md)) == md` — file to file. That is
idempotence, and idempotence cannot see loss: a lossy rendering is exactly as
stable as a correct one, which is why "20/20 idempotent" over a 20-CV corpus
passed while two real defects sat in the same pipeline. This test asks the other
question, DATA to file to DATA, and it asks it over shapes that are GENERATED
rather than collected: the corpus proved it never produces the shapes that break
us — 0 headless headings, 0 doubled prose lines, 0 `label:` lines in 140 records.
A corpus shows what happens to occur. Generated shapes show what is possible.

Every shape below is a real one, taken from a live document or from a live defect,
and each says which. Pure string work — no network, no LLM, no filesystem writes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ANCHOR = {"company": "Acme", "role": "PM", "period": "2019", "type": "employment"}

# (name, case, note) — `note` names the live defect or document the shape came from.
SHAPES = [
    ("place, thing and time",
     {"type": "employment", "company": "Acme", "role": "PM", "period": "2020–2024"},
     "the ordinary record"),

    ("time but no place",
     {"type": "credential", "role": "HTML/CSS. Интерактивный курс", "period": "2020"},
     "live: Казымов. 18 of 21 credentials name no issuer"),

    ("place but no time",
     {"type": "credential", "company": "Project Management Institute", "role": "PMBOK"},
     "a certificate whose year the CV never printed"),

    ("named only by label",
     {"type": "project", "label": "AI Health Assistant",
      "context": "Multi-agent architecture with persistent memory."},
     "live: candidate.json holds the name, the file rendered a bare ###"),

    ("prose of the record, and a first unnamed group with prose of its own",
     {"type": "employment", "company": "snaggd", "role": "Founder", "period": "2026",
      "context": "Desktop AI app for job search. Open core under MIT.",
      "highlights": [{"context": "Reads vacancies and writes a letter per posting.",
                      "results": ["500+ applications"]}]},
     "live: the record's own prose was overwritten on read-back"),

    ("a thing containing the separator, plus a domain",
     {"type": "employment", "company": "Northwind", "period": "2022–2026", "domain": "fintech",
      "role": "Product Manager | Fintech Platforms (B2B / B2C)"},
     "live: pm — zip() truncated the heading and shifted every slot"),

    ("a key of the person's own, in Cyrillic",
     {"type": "employment", "company": "Acme", "role": "PM", "period": "2020",
      "notes": {"Описание": "проектировал инструменты изнутри домена"}},
     "the ASCII key pattern drops this; the unicode one was never brought here"),

    ("duties and achievements together",
     {"type": "employment", "company": "Acme", "role": "PM", "period": "2020",
      "responsibilities": ["веду роадмап", "провожу дискавери"],
      "highlights": [{"label": "Checkout", "results": ["конверсия +30%"]}]},
     "on target_market=western the duties came back as achievements"),

    ("a record that stops at its heading",
     {"type": "education", "company": "University of Warsaw", "role": "Master", "period": "2018"},
     "live: 26 of 26 education records carry nothing below h3"),
]

# Blocks naming neither a place nor a time. The frame says these are not records —
# they are content of the record above. Written here as a second case following an
# anchor, because that is how they arrive in a document.
NON_RECORDS = [
    ("a progression with no dates",
     {"type": "employment", "role": "Analyst → Senior → Lead"},
     "western form; the CIS twin is `L2 Support → Back-office → Risk / Compliance`"),
    ("a side project named inside a job",
     {"type": "project", "label": "AI Health Assistant",
      "context": "Multi-agent architecture, end to end."},
     "live: floated out into ## Projects as a headless ###"),
]


def atoms(case):
    """Every piece of content that must survive the trip, flattened."""
    out = []
    for key, value in (case or {}).items():
        if key == "type":
            continue
        if isinstance(value, str) and value.strip():
            out.append((key, value.strip()))
        elif isinstance(value, dict):
            out += [(f"{key}.{k}", str(v).strip()) for k, v in value.items() if str(v).strip()]
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    out.append((key, item.strip()))
                elif isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, str) and v.strip():
                            out.append((f"{key}.{k}", v.strip()))
                        elif isinstance(v, list):
                            out += [(f"{key}.{k}", str(x).strip()) for x in v if str(x).strip()]
    return out


def values_of(node, out=None):
    """Every string that survived the trip, wherever it now sits."""
    out = [] if out is None else out
    if isinstance(node, str):
        if node.strip():
            out.append(node.strip())
    elif isinstance(node, dict):
        for value in node.values():
            values_of(value, out)
    elif isinstance(node, list):
        for item in node:
            values_of(item, out)
    return out


def survived(atom, came_back):
    """The question is loss, not placement — a value that landed in a different
    field is a separate (real) problem, but it is still readable. A value absent
    from the PARSED-BACK data is gone.

    Searching the re-parsed structure and not the rendered text, deliberately:
    checking the markdown tests only `data -> file`, which is the half that was
    never broken. Every known defect here destroys content on the way back. An
    earlier draft of this file made exactly that mistake and passed three shapes
    it should have failed."""
    return any(atom[1] in value for value in came_back)


def run():
    from onboarding.md_parse import parse_candidate_md
    from onboarding.resume_parser import ResumeData, ResumeParser
    from onboarding import profile_frame as frame

    failures = []

    def check(label, condition):
        print(f"  {'✅' if condition else '❌'} {label}")
        if not condition:
            failures.append(label)

    def roundtrip(cases, market="cis"):
        data = ResumeData(identity={"name": "Test", "role": "PM"},
                          cases=cases, target_market=market)
        md = ResumeParser(None).to_md(data, existing_content="")
        back = parse_candidate_md(md)
        return md, back, values_of(back.get("cases") or [])

    print("The discriminator: a block is a record only if it names its own place or time")
    for name, case, note in NON_RECORDS:
        check(f"not a record — {name}", not frame.is_record(case))
    for name, case, note in SHAPES:
        if case.get("company") or case.get("period"):
            check(f"is a record — {name}", frame.is_record(case))

    print("\nDATA to file to DATA: nothing a shape carries may disappear")
    for name, case, note in SHAPES:
        _, _, came_back = roundtrip([case])
        lost = [a for a in atoms(case) if not survived(a, came_back)]
        check(f"{name} — keeps everything  [{note}]", not lost)
        for field, value in lost:
            print(f"        lost {field}: {value[:64]}")

    print("\nThe same content must not change axis with the market")
    both = {"type": "employment", "company": "Acme", "role": "PM", "period": "2020",
            "responsibilities": ["веду роадмап"],
            "highlights": [{"label": "Checkout", "results": ["конверсия +30%"]}]}
    _, cis, _ = roundtrip([both], "cis")
    _, west, _ = roundtrip([both], "western")
    check("duties stay duties on both markets",
          (cis["cases"][0].get("responsibilities") or []) ==
          (west["cases"][0].get("responsibilities") or []))

    print("\nA block naming neither place nor time keeps its content anyway")
    for name, case, note in NON_RECORDS:
        _, _, came_back = roundtrip([dict(ANCHOR), case])
        lost = [a for a in atoms(case) if not survived(a, came_back)]
        check(f"{name} — content survives  [{note}]", not lost)
        for field, value in lost:
            print(f"        lost {field}: {value[:64]}")

    print("\nThe heading is decoded in one place, and it drops nothing")
    heading = "Northwind | PM | Platforms | 2022–2026 | fintech"
    slots, overflow = frame.parse_record_name(heading)
    # The property, not a count: a segment is either in a slot or in the overflow,
    # and either way it is still here. Pinning the number of overflow items would
    # pin how many slots we happen to read today, which is not the contract.
    check("no segment of a heading is dropped, whatever the slot count",
          set(slots.values()) | set(overflow) == {p.strip() for p in heading.split("|")})
    check("the WRITER commits to three slots, so it can never produce a fourth",
          frame.RECORD_SLOTS == ("company", "role", "period"))
    check("the READER still understands the fourth that every profile on disk carries",
          frame.parse_record_name("Acme | PM | 2020 | fintech")[0].get("domain") == "fintech")

    print("\nIndustry reaches the axis that is already told to weigh it")
    # prompts/match_scoring.md has said since the axis rebuild that "domain distance
    # lives here" — inside `experience` — and that there is no separate domain
    # adjustment anywhere. So no new axis, and no prompt change: AXES declares five,
    # and core/axes.py says calibration needs that set FROZEN.
    #
    # What was missing was not the rule but the value's shape. `domain` rode as an
    # unlabelled fourth segment of a heading, so the model had to infer that the
    # fourth thing after the pipes meant industry. It is a named key now, and this
    # pins that it survives the projection each reader gets — the scorer receives
    # candidate.md as text, so a field it cannot see is a field it cannot weigh.
    industry = {"type": "employment", "company": "Northwind", "role": "PM",
                "period": "2022–2026", "domain": "fintech"}
    md, _, _ = roundtrip([industry])
    for reader in frame.EVIDENCE_READERS:
        check(f"{reader} sees the industry, and sees it named",
              "domain: fintech" in frame.project_for(md, reader))

    print("\nUnder the token ceiling, a group is trimmed — not ignored, not emptied")
    big = {"type": "employment", "company": "Acme", "role": "PM", "period": "2020",
           "groups": [{"kind": "duties", "context": "First sentence. Second one. Third.",
                       "bullets": [f"duty {n}" for n in range(9)]}]}
    guarded = ResumeParser(None)._shorten_for_token_guard(
        ResumeData(identity={"name": "T", "role": "PM"}, cases=[big]))
    group = (guarded.cases[0].get("groups") or [{}])[0]
    check("the guard actually reaches a group at all",
          len(group.get("bullets") or []) != len(big["groups"][0]["bullets"]))
    check("bullets are capped, and the cap is the frame's not a literal",
          len(group.get("bullets") or []) == 3)
    check("prose is cut to its first sentence",
          group.get("context") == "First sentence.")
    check("and the record itself is never dropped to make room",
          guarded.cases[0].get("company") == "Acme")

    print()
    print(f"{'❌ ' + str(len(failures)) + ' failed' if failures else '✅ all passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
