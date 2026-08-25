"""Section-aware merge for candidate.md.

Why this exists, concretely (2026-08-14):

candidate.md is not a rendered view of candidate.json. It is the profile — the
one file every agent reads (scoring, cover letters, employer answers), and the
only place a person can express a preference the schema has no field for. A real
profile on disk carried, among other things:

    ## Career Profile
    not_looking_for: process_management, pmm, outsource

    ## Relocation & Work Format
    relocation_cities: city A (current), city B (ok), abroad (ok)
    work_format_priority: hybrid > remote > office

    ## Desired Salary
    telecom: 100 000+ net
    fintech: 150 000+ net
    note: ... input/modal → number only

None of those keys exist in ResumeData. The previous writer rendered the whole
managed block from the schema and kept only what sat after an end-marker
comment — and that profile had no markers at all, so "keep everything after the
marker" evaluated to "keep nothing". A single wizard save would have replaced
every line above with the schema's much poorer view of the same sections.

profile_guard did not cover it: it counts cases/skills/tools/languages, and its
own docstring says identity, salary and rules are deliberately excluded. A save
carrying a parsed resume passes that check while erasing every preference.

So the merge rule here is per section, and inside a keyed section per key:

    what the renderer emits, wins. What it does not emit, survives.

That rule only reads correctly because the renderer no longer emits placeholders
for absent values (it used to write `# HINT: add your city` and friends straight
into the file — and thus into the system prompt, which is why the scoring prompt
had to grow a clause about the word "SKIPPABLE"). With placeholders gone,
"absent from the rendered block" unambiguously means "the wizard has nothing to
say about this", and preserving is always the right answer.
"""

import re

from onboarding.profile_frame import (
    RETIRED_KEYS,
    DECLARED_SECTIONS, kind_for_heading, section_for_key,
)

# Written by the previous whole-block writer. No longer emitted; still stripped
# on read so a file written before 2026-08-14 migrates on its next save instead
# of keeping a comment nobody produces any more.
BLOCK_START = "<!-- snaggd:start -->"
BLOCK_END = "<!-- snaggd:end -->"

# `key: value`. Deliberately strict — `hints (low-confidence — verify …):` is
# NOT a key, and must not be treated as one, or its bullet list underneath would
# be orphaned from the line that introduces it.
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s|$)")

# Sections whose bodies are key/value lines. These merge per key, so a key the
# schema has never heard of (not_looking_for, relocation_cities, note) survives.
# Everything else is a block section — a list or a run of `###` case blocks —
# where a partial merge would produce nonsense, so the rendered body replaces
# the existing one wholesale when there is one, and leaves it alone when there
# is not.
KEYED_SECTIONS = frozenset({
    "Identity",
    "Career Profile",
    "Relocation & Work Format",
    "Desired Salary",
    "Tools",
    "Languages",
    "Additional",
})


def _is_evidence(body: list[str]) -> bool:
    """A section holding `### ` blocks. That shape, not the heading's spelling, is
    what makes a section part of the evidence the renderer owns."""
    return any(line.startswith("###") and not line.startswith("####") for line in body)


def _key_of(line: str) -> str | None:
    m = _KEY_RE.match(line)
    return m.group(1) if m else None


def _dissolve_undeclared(sections: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    """A heading the frame never declared stops existing; its lines go home.

    `## Tools & Languages` and `## Contacts & Personal` are real headings from a
    real profile. Nothing was wrong with them until a CV arrived and wrote the
    canonical sections beside them, leaving the file asserting two tool lists and
    both `english: B2` and `english: C1` — with the model reading the lot as facts
    about one person. Recognising the spellings would be a synonym table: one row
    per heading anyone ever invents, and the cause untouched, since the heading
    would still BE the slot.

    So placement is by key, exactly as evidence is placed by kind. Each line goes
    to the section that owns its key; the leftovers go to the one section with an
    open vocabulary, because a language is called whatever it is called and cannot
    be listed in advance. Then the ordinary per-key merge decides who wins, which
    is where a rendered `english: C1` displaces a stale B2 without touching the
    `russian: native` nobody restated.

    Evidence sections are not touched here — they are placed by kind, upstream.
    """
    out: list[tuple[str, list[str]]] = []
    routed: dict[str, list[str]] = {}
    for heading, body in sections:
        if heading in DECLARED_SECTIONS or kind_for_heading(heading) or _is_evidence(body):
            out.append((heading, body))
            continue
        # Lines of this section that own no key, and so stay under it.
        stays: list[str] = []
        for line in body:
            if not line.strip():
                continue
            key = _key_of(line)
            # A key WE retired is dropped, not rehomed. Without this it would fall
            # through to the open-vocabulary section — `role_type: hands-on builder`
            # filed under `## Languages` on the next save of every profile still
            # carrying it. Dissolving is for a person's own heading; this is our own
            # deletion, and it has to land as one. See profile_frame.RETIRED_KEYS.
            if key in RETIRED_KEYS:
                continue
            # A line with no key has nothing to place it BY — and nothing to
            # conflict with either. Until 2026-08-26 it was swept into
            # `## Additional`, which cost the person the frame they wrote: that
            # section holds interests and `hints (low-confidence — verify before
            # relying on)`, so a heading someone added deliberately reached the
            # letter writer labelled as a hedge.
            #
            # Dissolving exists for a heading that COMPETES with a declared one —
            # `## Tools & Languages` beside `## Tools`, two english levels, both
            # read as facts. Competition is what a key makes: a key has an owner,
            # so two of them collide. An unkeyed line owns no slot and collides
            # with nothing, so its heading stays and the lines stay under it.
            if key is None:
                stays.append(line)
                continue
            routed.setdefault(section_for_key(key), []).append(line)
        # In place, not appended at the end. Collecting these and adding them after
        # the loop moved the section to the bottom on the second save while its
        # content stayed identical — stable bytes on the first save, different on
        # the next, which is idempotence failing in the one way a diff catches and
        # a content check does not.
        if stays:
            out.append((heading, stays))

    for target, lines in routed.items():
        for i, (heading, body) in enumerate(out):
            if heading == target:
                out[i] = (heading, list(body) + lines)
                break
        else:
            out.append((target, lines))
    return out


def split_tail(markdown: str) -> tuple[str, str]:
    """Everything after the legacy end-marker is a verbatim tail.

    The old writer's one real guarantee — free text a person appended below the
    generated block survives a re-run — is kept exactly, rather than being
    re-derived by the section logic below and possibly landing somewhere else.
    """
    if BLOCK_END in markdown:
        head, tail = markdown.split(BLOCK_END, 1)
        return head, tail
    return markdown, ""


def parse_sections(markdown: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """`## Heading` sections, in file order, plus whatever precedes the first one.

    The preamble is normally the `# <target role>` title line.
    """
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None

    for line in markdown.splitlines():
        if line in (BLOCK_START, BLOCK_END):
            continue
        if line.startswith("## "):
            current = []
            sections.append((line[3:].strip(), current))
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)

    return preamble, sections


def _merge_keyed(existing_body: list[str], rendered_body: list[str]) -> list[str]:
    """Per-key merge. Rendered keys win; unknown existing keys are kept in place.

    Blank lines are dropped here (a key/value block has no use for them) —
    unlike a block section, whose body is kept verbatim.
    """
    rendered_keyed: dict[str, str] = {}
    rendered_loose: list[str] = []
    for line in rendered_body:
        if not line.strip():
            continue
        key = _key_of(line)
        if key:
            rendered_keyed[key] = line
        else:
            rendered_loose.append(line)

    # Values the render states behind a key — used to spot an existing bare line
    # that says the same thing without one.
    rendered_values = {l.split(":", 1)[1].strip() for l in rendered_keyed.values() if ":" in l}

    out: list[str] = []
    used: set[str] = set()
    seen: set[str] = set()
    for line in existing_body:
        if not line.strip():
            continue
        key = _key_of(line)
        if key:
            # One line per key. A section can hold the same key twice once lines have
            # been routed into it from a dissolved section — `## Contacts & Personal`
            # arriving in an Identity that already had a telegram — and without this
            # both survived, then both got replaced by the rendered value, so the
            # duplicate outlived the heading that caused it. First occurrence wins:
            # it is the one already in the canonical section, and the routed copy is
            # by construction the older statement.
            if key in seen:
                continue
            seen.add(key)
            if key in rendered_keyed:
                out.append(rendered_keyed[key])
                used.add(key)
            else:
                out.append(line)
                # The mirror of the bare-line case below: this line states behind
                # a key exactly what the render is about to state without one, so
                # the render's copy is dropped and the key survives. A label is
                # information — it is what routes the line to its section and what
                # lets the next merge match it at all.
                _value = line.split(":", 1)[1].strip() if ":" in line else None
                if _value:
                    rendered_loose = [l for l in rendered_loose if l.strip() != _value]
        elif line.strip() in rendered_values:
            # The same content, once as a bare line and once behind a key. That is
            # what a section looks like the first time it is saved after its format
            # gained a key — `## Tools` held `Jira, Figma` and now renders
            # `tools: Jira, Figma` — and keeping both leaves the value stated twice.
            # Exact match only: no guessing about what an unkeyed line might mean.
            continue
        elif not rendered_loose:
            out.append(line)
        # else: the renderer has its own unkeyed content for this section (a
        # bare salary line, an interests/hints run) — it replaces the existing
        # unkeyed content rather than accumulating beside it.

    out += [line for key, line in rendered_keyed.items() if key not in used]
    out += rendered_loose
    return out


def merge(existing: str, rendered: str) -> str:
    """Merge a freshly rendered managed block into an existing candidate.md.

    `existing` may be empty (first save), marker-less (hand-written or restored
    by hand), or carry the pre-2026-08-14 markers. All three converge on the
    same output shape: sections in the file's own order, unknown sections and
    unknown keys intact, the legacy tail appended verbatim.
    """
    existing_head, tail = split_tail(existing or "")
    old_preamble, old_sections = parse_sections(existing_head)
    new_preamble, new_sections = parse_sections(rendered)

    preamble = new_preamble if any(p.strip() for p in new_preamble) else old_preamble

    # Evidence is owned whole. A save that carries any of it has already decided,
    # per kind, what survives (ResumeParser._carry_unclaimed_evidence) — so an
    # evidence section still sitting in the file that this render did not produce is
    # a stale copy, whatever it happens to be called. Leaving it is how one profile
    # came to hold the same two projects twice, under two headings, with the model
    # reading both. A `### ` block is what makes a section evidence; a prose or
    # key/value section is not touched by this and merges as it always did.
    rendered_headings = {h for h, _ in new_sections}
    if any(_is_evidence(b) for _, b in new_sections):
        old_sections = [(h, b) for h, b in old_sections
                        if h in rendered_headings or not _is_evidence(b)]

    old_sections = _dissolve_undeclared(old_sections)

    merged: list[tuple[str, list[str]]] = [(h, list(b)) for h, b in old_sections]
    index = {h: i for i, (h, _) in enumerate(merged)}

    for heading, body in new_sections:
        if heading not in index:
            index[heading] = len(merged)
            merged.append((heading, list(body)))
        elif heading in KEYED_SECTIONS:
            i = index[heading]
            merged[i] = (heading, _merge_keyed(merged[i][1], body))
        else:
            merged[index[heading]] = (heading, list(body))

    out: list[str] = [p for p in preamble if p.strip()]
    for heading, body in merged:
        body = [line for line in body]
        while body and not body[-1].strip():
            body.pop()
        if not body:
            # A section with nothing left in it is not written at all. An empty
            # `## Heading` in the profile is one more thing for the model to
            # read and make something of.
            continue
        out += ["", f"## {heading}"] + body

    body = "\n".join(out).lstrip("\n")
    if tail:
        # The tail is verbatim, trailing newline and all — it is the user's text.
        return body + tail
    return body + "\n"
