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

from onboarding.profile_frame import kind_for_heading

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")

# `_typed_contact_line()`'s labels, inverted. Anything else in the Identity
# section that is not one of these and not a known key is the pitch.
_CONTACT_KEYS = {"telegram", "github", "linkedin", "email", "phone"}

# Sections that are prose or key/value rather than evidence. A heading outside
# both this set and the frame is a person's own heading over `###` blocks, and
# becomes evidence of kind `other` — captured, not guessed at, not lost.
_NON_EVIDENCE_SECTIONS = frozenset({
    "Identity", "Career Profile", "Relocation & Work Format",
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
    out = {}
    for line in body:
        m = _KEY_RE.match(line.strip())
        if m:
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
        if line.startswith("### "):
            close_highlight()
            parts = [p.strip() for p in line[4:].split("|")]
            keys = ("company", "role", "period", "domain")
            case = {k: v for k, v in zip(keys, parts) if v}
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
            else:
                case["context"] = ctx
            continue
        keyed = _KEY_RE.match(line)
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
        if line.startswith("- "):
            item = line[2:].strip()
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
            if m and m.group(1) == "name":
                identity["name"] = m.group(2).strip()
            elif m and m.group(1) == "location":
                identity["location"] = m.group(2).strip()
            elif m and m.group(1) in _CONTACT_KEYS:
                contacts.append(m.group(2).strip())
            else:
                # `_typed_contact_line` passes an unrecognized contact through
                # unlabeled, and the pitch is unlabeled too. A pitch is prose;
                # a bare contact is a single token.
                (contacts if len(line.split()) == 1 else pitch_lines).append(line)
    if contacts:
        identity["contacts"] = contacts
    if identity:
        data["identity"] = identity
    if pitch_lines:
        data["pitch"] = "\n".join(pitch_lines)

    career = _keyed(sec.get("Career Profile", []))
    cp = {k: career[k] for k in ("role_type", "edge", "aspiration") if career.get(k)}
    if cp:
        data["career_profile"] = cp
    if career.get("not_looking_for"):
        data["rules"] = {"penalize": _split_list(career["not_looking_for"])}

    logistics = _keyed(sec.get("Relocation & Work Format", []))
    lg = {k: logistics[k] for k in ("relocation", "work_format") if logistics.get(k)}
    if lg:
        data["logistics"] = lg

    salary = "\n".join(l for l in sec.get("Desired Salary", []) if l.strip())
    if salary:
        data["search"] = {"salary": salary}

    skills = [l.strip()[2:].strip() for l in sec.get("Skills", []) if l.strip().startswith("- ")]
    if skills:
        data["skills"] = skills

    tools_body = [l.strip() for l in sec.get("Tools", []) if l.strip()]
    if tools_body:
        # `tools: a, b` now; a bare `a, b` line is what older files hold.
        m = _KEY_RE.match(tools_body[0])
        data["tools"] = _split_list(m.group(2) if m and m.group(1) == "tools" else tools_body[0])

    languages = []
    for raw in sec.get("Languages", []):
        m = _KEY_RE.match(raw.strip())
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
        if any(l.strip().startswith("### ") for l in body):
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


def merge_over_json(markdown: str, candidate_json: dict | None) -> dict:
    """What the wizard should open with.

    The markdown wins wherever it says anything, because it is the profile. The
    JSON supplies only what the markdown cannot carry — schema_version, locale,
    target_market, the finer case `type` values — and fills a section the
    markdown does not have at all.
    """
    base = dict(candidate_json or {})
    parsed = parse_candidate_md(markdown)

    for key, value in parsed.items():
        if key in ("identity", "career_profile", "logistics", "search", "rules") and isinstance(base.get(key), dict):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        else:
            base[key] = value
    return base
