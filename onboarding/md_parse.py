"""candidate.md → ResumeData-shaped dict. The inverse of ResumeParser.to_md().

Why this exists, concretely (2026-08-14):

candidate.md is the profile. candidate.json holds the wizard's saved answers so a
re-run can prefill instead of opening blank. When the two disagree, the markdown
is right — and they can disagree badly: profile `pm` carried 104 lines of real
content in candidate.md next to a candidate.json whose every field was null,
left behind by a wizard save that emptied it. Opening the wizard on that profile
showed an empty seven-step form, because the wizard read the JSON.

An empty form over a full profile is not a display bug. It is one click away from
being the profile, which is exactly how the JSON got emptied in the first place.
profile_guard stops the write; it cannot make the form correct.

So the wizard prefills from here instead, and the constructor becomes what it
looked like all along: two views — a stepped form and a text editor — of one
document. Editing either one edits the profile, and neither erases what the other
put there.

The mechanism that keeps this honest is a round trip, not a reading: parse a
rendered file and render it again, and you must get the same bytes back
(tests/test_candidate_md_roundtrip.py runs it against the real profile).

Known limits, both structural rather than bugs:
  - `### Company | Role | Period | Domain` is positional. With fewer than four
    parts the split is a guess, and this makes the documented one (company, then
    role, then period).
  - `to_md` renders education and the project family into two headings, so the
    finer `type` values inside the project family (certification, publication,
    volunteering, research) are not recoverable from the text. They come back as
    "project"; candidate.json keeps the original when it has one.
"""

import re

from onboarding import profile_frame as frame
from onboarding.profile_frame import kind_for_heading

# A key inside a record's body. Unicode by design: `_KEY_RE` is ASCII, so a line a
# Russian-speaking person wrote — `Описание: …` — matched nothing and fell out of the
# loop unread. The permissive `_OPEN_KEY_RE` is not the right tool here either; it
# would turn any prose sentence containing a colon into a key. One word, any script.
_CASE_KEY_RE = re.compile(r"^([^\W\d_]\w*):\s*(.*)$", re.UNICODE)

# Either bullet marker. A file is edited by hand and by other editors, and which of
# the two characters a person's tool inserts says nothing about their content.
_BULLET_RE = re.compile(r"^[-*]\s+")

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")
# The open-vocabulary section names its own keys, and a language is called whatever it
# is called — in whatever script it is called it in. `_KEY_RE` is ASCII by design,
# because every key the frame declares is one of ours and ASCII; applying it to
# Languages meant `Русский: Родной` matched nothing and the whole section was dropped
# on the way back in.
#
# Found 2026-08-25 by the frame's own idempotence invariant, over 20 real CVs: the
# first save wrote the section and the second one lost it, 19 documents out of 20.
# It had been invisible because the profiles it was measured on all wrote language
# names in Latin. On a product whose users write their CVs in Russian, this silently
# removed the languages from every profile — and Languages is read by the SCORER,
# where for some jobs it is the requirement.
#
# Deliberately narrow: this pattern is used for Languages and nowhere else. Widening
# `_KEY_RE` itself would make any Russian prose line with a colon in it read as a key
# — "Опыт работы: 5 лет" in a hand-written Identity section would become a slot.
_OPEN_KEY_RE = re.compile(r"^([^\s:][^:]*):\s*(.*)$")
_EMPTY_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:\s*$")

# `_typed_contact_line()`'s labels, inverted. Anything else in the Identity
# section that is not one of these and not a known key is the pitch.
_CONTACT_KEYS = {"telegram", "github", "linkedin", "email", "phone"}

# Sections that are prose or key/value rather than evidence. A heading outside
# both this set and the frame is a person's own heading over `###` blocks, and
# becomes evidence of kind `other` — captured, not guessed at, not lost.
# `Career Profile` is kept in this set after being retired as a heading (2026-08-25):
# a profile written before the split still carries it, and its lines are keys we still
# read. Listing it here is what stops those lines being mistaken for evidence of kind
# `other` on the way in — the keys themselves are placed by KEY_OWNERS on the way out,
# so stop_categories lands in Constraints and not_looking_for in Preferences without a
# rule of their own.
_NON_EVIDENCE_SECTIONS = frozenset({
    "Identity", "Constraints", "Preferences", "Career Profile",
    "Relocation & Work Format",
    "Desired Salary", "Skills", "Tools", "Languages", "Additional",
})


def _sections(markdown: str) -> tuple[str, dict[str, list[str]]]:
    """Preamble title plus `## Heading` → body lines, in file order."""
    title = ""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in markdown.splitlines():
        if line.startswith("## "):
            current = out.setdefault(line[3:].strip(), [])
        elif line.startswith("# ") and current is None:
            title = line[2:].strip()
        elif current is not None:
            current.append(line)
    return title, out


def _keyed(body: list[str]) -> dict[str, str]:
    """`key: value` lines, skipping keys that carry no value.

    A named slot with nothing in it is structure, not an answer — the same
    reading profile_guard's skeleton predicate takes, and the same one the
    prompting layer already takes of an absent field (absence is neutral, it is
    not evidence of a mismatch). Without it, a blank profile that names its slots
    and fills none of them parses into `name: ""`, `location: ""` and a desired
    salary of `default:` — answers nobody gave.
    """
    out = {}
    for line in body:
        m = _KEY_RE.match(line.strip())
        if m and m.group(2).strip():
            out[m.group(1)] = m.group(2).strip()
    return out


def _split_list(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def _parse_cases(body: list[str], case_type) -> list[dict]:
    """`### header` blocks, mirroring ResumeParser._render_case()."""
    cases: list[dict] = []
    case: dict | None = None
    highlight: dict | None = None
    in_zone = False

    def close_highlight():
        nonlocal highlight
        if case is not None and highlight is not None:
            case.setdefault("highlights", []).append(highlight)
        highlight = None

    for raw in body:
        line = raw.strip()
        if not line:
            continue
        # A case boundary is the `###` itself, with or without text after it.
        #
        # `startswith("### ")` — with the space — until 2026-08-25, while the renderer writes a
        # BARE `###` for a case nothing names (see resume_parser._render_case: "content that
        # does not exist is not written"). The two disagreed, and the failure was not that the
        # headless case was lost: it was silently absorbed into the PREVIOUS case, whose url
        # and context it then overwrote. Two entries became one wrong one.
        #
        # Found on the first profile created through the wizard after the frame rework, by the
        # corpus test's own round-trip check — a project entry with a url and a description and
        # no company, role or period, which is the ordinary shape of a side project.
        if line.startswith("###") and not line.startswith("####"):
            close_highlight()
            # Decoded by the frame, which is also where it is encoded — one pair,
            # so the two halves cannot drift apart again. What stood here was
            # `zip(("company","role","period","domain"), parts)`, and zip truncates
            # to the shorter side in silence: a heading with more segments than
            # slots lost its tail, and on live profile `pm` every slot shifted by
            # one, so the employment period was read as the domain. `domain` is a
            # declared key below the heading now, not a fourth position on it.
            slots, overflow = frame.parse_record_name(line[4:])
            case = dict(slots)
            if overflow:
                # Nothing a person wrote may vanish because our line had fewer
                # slots than their heading had parts.
                case.setdefault("notes", {})["heading"] = " | ".join(overflow)
            if case_type:
                case["type"] = case_type
            cases.append(case)
            in_zone = False
            continue
        if case is None:
            continue
        # `####` on its own is the boundary between groups of bullets inside one
        # entry, and it is all it is. Text after it is read for two reasons and
        # two only: the frame's own `Zone of Responsibility`, and a file written
        # before 2026-08-17, when the group's name lived in this line instead of
        # on a `label:` below it. Read tolerantly, write strictly — one save
        # converts the old shape, so this is one legacy form, not a table of them.
        if line.startswith("####"):
            label = line[4:].strip()
            close_highlight()
            if label == "Zone of Responsibility":
                in_zone = True
            else:
                in_zone = False
                highlight = {"results": []}
                if label:
                    highlight["label"] = label
            continue
        if line.startswith("url: "):
            case["url"] = line[5:].strip()
            continue
        if line.startswith("Context: "):
            ctx = line[9:].strip()
            if highlight is not None:
                highlight["context"] = ctx
            elif case.get("context"):
                # Two prose lines at the record's own level. The renderer no longer
                # writes this shape — a group after the record's prose is bounded
                # now — but files written before 2026-08-26 carry it, and the second
                # line used to REPLACE the first. Live: snaggd lost the sentence
                # saying what it is. Keep both; a reader below reads prose, not slots.
                case["context"] = f"{case['context']} {ctx}"
            else:
                case["context"] = ctx
            continue
        keyed = _CASE_KEY_RE.match(line)
        if keyed and highlight is None and keyed.group(1) in frame.NON_SLOT_RECORD_KEYS:
            case[keyed.group(1)] = keyed.group(2).strip()
            continue
        # The group's name, now that it is content rather than a heading.
        if keyed and keyed.group(1) == "label" and highlight is not None:
            highlight["label"] = keyed.group(2).strip()
            continue
        if keyed and highlight is None:
            # A prose line a person wrote themselves — `Architecture: …`, `Stack: …`.
            # The schema has one prose slot per case (`context`) and a real profile
            # uses several, so the rest are kept as they were written and rendered
            # back in order. Same rule as unknown keys inside a section: what the
            # renderer has no field for still belongs to the person who wrote it.
            case.setdefault("notes", {})[keyed.group(1)] = keyed.group(2).strip()
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            item = line[bullet.end():].strip()
            if in_zone:
                case.setdefault("responsibilities", []).append(item)
                continue
            if highlight is None:
                # `_render_case` writes no `####` line for a highlight that has no
                # label, so its results arrive as bare bullets under the `###`
                # header. Dropping them here lost a whole case's content on a
                # profile whose bullets were never labelled.
                highlight = {"results": []}
            highlight["results"].append(item)
            continue
        # The else-branch this loop never had. A body line matching none of the
        # branches above used to fall out of the bottom and disappear — no
        # exception, no log, nothing in the file to show it had been there. That
        # is the single node every defect of 2026-08-26 grew from, and it is
        # exactly the failure mode a guard cannot report on its own.
        #
        # Prose is the honest home for it: the record already has a prose level,
        # and a line we could not classify is still something the person wrote.
        target = highlight if highlight is not None else case
        target["context"] = f"{target['context']} {line}" if target.get("context") else line

    close_highlight()
    return cases


def parse_candidate_md(markdown: str) -> dict:
    """Everything `to_md` writes, read back. Absent sections are simply absent."""
    title, sec = _sections(markdown or "")
    data: dict = {}

    identity: dict = {}
    if title:
        identity["role"] = title
    contacts: list[str] = []
    pitch_lines: list[str] = []
    if "Identity" in sec:
        for raw in sec["Identity"]:
            line = raw.strip()
            if not line:
                continue
            m = _KEY_RE.match(line)
            # A key with nothing after it names a slot and fills none of it — see
            # _keyed. Empty contact keys used to arrive as empty contacts.
            if m and not m.group(2).strip():
                continue
            if m and m.group(1) == "name":
                identity["name"] = m.group(2).strip()
            elif m and m.group(1) == "location":
                identity["location"] = m.group(2).strip()
            elif m and m.group(1) in _CONTACT_KEYS:
                contacts.append(m.group(2).strip())
            elif len(line.split()) == 1:
                # A single token is a contact — and this test has to come BEFORE the
                # keyed one below, not after it. A URL contains a colon, so
                # `https://example.com/x` matches the key pattern with `https` as its
                # key; ordered the other way it stopped being read as a contact at
                # all. `_typed_contact_line` passes an unknown contact through
                # unlabelled by design, which is what makes this the right test.
                contacts.append(line)
            elif m:
                # A `key: value` line whose key this section does not own. It is
                # NOT prose, so it is not the pitch — and until 2026-08-25 it was:
                # the test below was word count alone, so `role: Product Manager`
                # (three words) became someone's elevator pitch. Live profile `pm`
                # carried four such strays as its pitch — role, experience_years,
                # current_company, domain — and the letter writer read them as the
                # person's own introduction.
                #
                # Nothing is done with it here on purpose. It stays in the file,
                # under the heading the person can see, because the renderer does
                # not emit it and _merge_keyed therefore preserves it. Guessing a
                # home for it is what the frame refuses to do; making it invisible
                # by filing it as prose is worse than leaving it visible.
                pass
            else:
                # Prose, and therefore the pitch: not a key this section owns, not a
                # key at all, not a single token.
                pitch_lines.append(line)
    if contacts:
        # The same contact named twice is one contact. It arrives twice on the
        # save after a value the type-sniffer could not label: the sniffer
        # returns it unlabeled by design, the keyed line it came from survives
        # the merge beside it, and the next read counts both. Left alone the
        # list grew by one every single save — 0, 1, 2, 3 — on a profile whose
        # phone had been partly masked by hand. Order is the file's.
        identity["contacts"] = list(dict.fromkeys(contacts))
    if identity:
        data["identity"] = identity
    if pitch_lines:
        data["pitch"] = "\n".join(pitch_lines)

    # Two sections since 2026-08-25, plus the retired heading they were split out of.
    # Read from wherever the line currently sits: a profile on disk can be in either
    # shape, and a save canonicalises it. role_type/edge/aspiration are NOT read back
    # — they were deleted from the frame, so a profile still carrying them loses them
    # on the next save, which is the migration and is intended.
    refusals = _keyed(sec.get("Constraints", []))
    soft = _keyed(sec.get("Preferences", []))
    legacy = _keyed(sec.get("Career Profile", []))
    rules = {}
    for src in (legacy, refusals):
        if src.get("stop_categories"):
            rules["stop_categories"] = _split_list(src["stop_categories"])
    for src in (legacy, soft):
        if src.get("not_looking_for"):
            rules["penalize"] = _split_list(src["not_looking_for"])
    if rules:
        data["rules"] = rules

    logistics = _keyed(sec.get("Relocation & Work Format", []))
    lg = {k: logistics[k] for k in ("relocation", "work_format") if logistics.get(k)}
    if lg:
        data["logistics"] = lg

    # Same rule as _keyed: a key with no value is a slot, not a number.
    salary = "\n".join(l for l in sec.get("Desired Salary", [])
                       if l.strip() and not _EMPTY_KEY_RE.match(l.strip()))
    if salary:
        data["search"] = {"salary": salary}

    # Two shapes, because two exist in the wild and only one of them was read.
    # `to_md` writes skills as bullets and tools as a `tools:` line — the same
    # kind of list, in two shapes, sitting next to each other in the file. So a
    # person hand-editing Skills writes what Tools taught them, `skills: a, b`,
    # and until 2026-08-17 that vanished entirely: nothing here matched, the key
    # went nowhere, and the profile silently lost every skill it had. Read both.
    # (Which shape the writer should emit is a separate, deliberate decision —
    # changing it rewrites a section in every existing file.)
    skills = [l.strip()[2:].strip() for l in sec.get("Skills", []) if l.strip().startswith("- ")]
    if not skills:
        for raw in sec.get("Skills", []):
            line = raw.strip()
            if not line:
                continue
            m = _KEY_RE.match(line)
            skills = _split_list(m.group(2) if m and m.group(1) == "skills" else line)
            break
    if skills:
        data["skills"] = skills

    tools_body = [l.strip() for l in sec.get("Tools", []) if l.strip()]
    if tools_body:
        # `tools: a, b` now; a bare `a, b` line is what older files hold.
        m = _KEY_RE.match(tools_body[0])
        data["tools"] = _split_list(m.group(2) if m and m.group(1) == "tools" else tools_body[0])

    languages = []
    for raw in sec.get("Languages", []):
        m = _OPEN_KEY_RE.match(raw.strip())
        if not m:
            continue
        value = m.group(2).strip()
        note = None
        if value.endswith(")") and "(" in value:
            value, note = value[:value.rindex("(")].strip(), value[value.rindex("(") + 1:-1].strip()
        lang = {"lang": m.group(1), "level": value}
        if note:
            lang["note"] = note
        languages.append(lang)
    if languages:
        data["languages"] = languages

    cases: list[dict] = []
    for heading, body in sec.items():
        if heading in _NON_EVIDENCE_SECTIONS:
            continue
        kind = kind_for_heading(heading)
        if kind is not None:
            cases += _parse_cases(body, kind)
            continue
        # A heading the frame never declared. If it has `### ` blocks under it,
        # it is evidence someone organised their own way: keep the items and keep
        # the heading text as the label, so a person (or, when they ask for it, a
        # model) can place them later. A prose section with no blocks is left
        # alone — md_merge preserves it verbatim, and inventing structure for it
        # would be the guessing this frame exists to avoid.
        if any(l.strip().startswith("###") and not l.strip().startswith("####") for l in body):
            for case in _parse_cases(body, "other"):
                case["label"] = heading
                cases.append(case)
    if cases:
        data["cases"] = cases

    interests, hints, in_hints = [], [], False
    for raw in sec.get("Additional", []):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("interests:"):
            interests = _split_list(line.split(":", 1)[1])
            in_hints = False
        elif line.startswith("hints "):
            in_hints = True
        elif in_hints and line.startswith("- "):
            hints.append(line[2:].strip())
    if interests:
        data["interests"] = interests
    if hints:
        data["hints"] = hints

    return data


# What `candidate.md` genuinely cannot express, and therefore the only things the
# wizard's saved answers are allowed to contribute when it opens.
#
# Declared as a closed list on 2026-08-25, replacing "the JSON fills a section the
# markdown does not have at all". That sentence was an exception list wearing a rule,
# and it is how a hand edit got undone: delete a section from the profile, open the
# wizard, and the JSON put it back — from a copy nothing had updated since the last
# save. The person could not tell which of their edits would stick, which is a worse
# failure than losing one, because it makes the whole file untrustworthy.
#
# The discriminant is not "which keys are legacy" but "which keys this FORMAT cannot
# carry". Bookkeeping about the parse, and the two rules that live in filters.json and
# search_urls.txt, are absent from the markdown because it has nowhere to put them —
# not because the person removed them.
_CARRIED_FROM_JSON = frozenset({
    "schema_version", "locale", "target_market",
    "source_file", "parsed_at", "hints", "suggested_queries",
})
# Same rule one level down, for sections the markdown owns only part of.
_CARRIED_SUBKEYS = {
    "search": frozenset({"queries"}),          # search_urls.txt
    "rules": frozenset({"stop", "min_employer_rating"}),  # filters.json
}


def merge_over_json(markdown: str, candidate_json: dict | None) -> dict:
    """What the wizard should open with.

    The markdown is the profile, so it wins — including where it is SILENT. A key
    the markdown no longer states is a key the person removed, and the wizard has to
    open without it or the next save writes it back.

    The JSON contributes only what the markdown cannot carry (see the two lists
    above). It is the wizard's saved answers, not a second copy of the profile.
    """
    base = dict(candidate_json or {})
    parsed = parse_candidate_md(markdown)

    out = {k: v for k, v in base.items() if k in _CARRIED_FROM_JSON}
    for key, value in parsed.items():
        carried = _CARRIED_SUBKEYS.get(key)
        if carried and isinstance(base.get(key), dict) and isinstance(value, dict):
            merged = {k: v for k, v in base[key].items() if k in carried}
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value

    # A section the markdown says nothing about can still hold a carried key — the
    # rules live in filters.json, and a profile with no refusals written into
    # candidate.md may still have a company stop list.
    for key, carried in _CARRIED_SUBKEYS.items():
        if key not in out and isinstance(base.get(key), dict):
            kept = {k: v for k, v in base[key].items() if k in carried}
            if kept:
                out[key] = kept
    return out
