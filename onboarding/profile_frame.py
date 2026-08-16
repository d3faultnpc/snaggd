"""The frame: what kinds of evidence a profile can hold, and what they are called.

One declaration, used by the renderer (resume_parser.to_md), the reader
(md_parse) and anything that needs to group cases. Two copies of this map is how
a section stops being the same section on the way back.

Why a frame at all (2026-08-14):

A profile's evidence used to be rendered into three headings — Work Experience,
Education, Projects & Credentials — while the schema carried seven distinct
kinds. Reading the file back could therefore only ever recover three, so a
diploma and a professional-requalification certificate came out identical, and a
hand-written heading like `## Side Projects` matched none of the three and became
a section nothing structured could see.

The fix is not a table of accepted heading spellings. That would grow by one row
per heading anyone ever invents, and it leaves the real problem in place: the
heading string IS the semantic slot, so inventing a heading invents a slot. Here
the slot is `kind`, from a closed vocabulary, and the heading is derived from it.
A heading nobody declared is not a synonym to guess — it is evidence of kind
`other`, carrying its own text as a label, and reclassifying it is a suggestion a
person accepts rather than something inferred behind their back.

What the vocabulary is built from: the two kinds every schema in the field agrees
on (employment, education); the credential family that everything except an ATS
keeps separate from education (JSON Resume `certificates`, Textkernel
`Certifications`/`Licenses`, LinkedIn "Licenses & certifications", hh.ru's own
"повышение квалификации"); and a tail that every schema has and no two slice the
same way — which is the argument for naming it once, closing it, and giving it an
honest `other` rather than a section per fashion.
"""

# Ordered: this is also the order sections appear in a rendered profile.
KIND_HEADINGS: dict[str, str] = {
    "employment": "Work Experience",
    "education": "Education",
    "credential": "Certificates & Courses",
    "project": "Projects",
    "publication": "Publications",
    "volunteering": "Volunteering",
    "award": "Awards",
    "other": "Other",
}

HEADING_KINDS: dict[str, str] = {h: k for k, h in KIND_HEADINGS.items()}

# Kinds that were once in the vocabulary, mapped onto the one that replaced them.
# A migration artifact: finite, one-directional, and it shrinks. Nothing is added
# here for a value a person or a model invented — those become `other`.
RETIRED_KINDS: dict[str, str] = {
    "certification": "credential",
    "research": "project",
}

# Headings this renderer used to write and no longer does, because their contents
# are now placed by kind. Read for their content, then dropped — otherwise a save
# would leave the old section sitting beside the new ones holding a stale copy.
# The kind is the best available reading: everything the old heading held was
# rendered from the project family, and `project` is its overwhelming member.
RETIRED_HEADINGS: dict[str, str] = {
    "Projects & Credentials": "project",
}

# ── The other half of the frame: sections that hold keys, not evidence ───────
#
# Evidence is placed by `kind`. Everything else is placed by KEY — same move, same
# reason. A person writing their profile by hand invents a heading (`## Tools &
# Languages`, `## Contacts & Personal`) and the file then holds two names for one
# thing; a CV upload writes the canonical one and the two sit there contradicting
# each other, which is how one profile came to state `english: B2` and `english: C1`
# at once. Matching heading spellings would be a synonym table, growing a row per
# invented heading and leaving the cause (the heading IS the slot) untouched. So an
# undeclared section is dissolved instead: each of its lines goes to the section
# that owns that key, and the heading simply stops existing.
KEY_OWNERS: dict[str, str] = {
    "name": "Identity",
    "location": "Identity",
    "telegram": "Identity",
    "github": "Identity",
    "linkedin": "Identity",
    "email": "Identity",
    "phone": "Identity",
    "role_type": "Career Profile",
    "edge": "Career Profile",
    "aspiration": "Career Profile",
    "not_looking_for": "Career Profile",
    # Semantic categories the person refuses outright. A preference of theirs, so it
    # belongs in the file that describes them — and it has to be THIS file, because
    # the model reads it here and the block validator reads the same line, instead of
    # two copies in two files drifting apart. The machine tier (exact company and
    # title matches) stays in filters.json, which never reaches the model at all.
    "stop_categories": "Career Profile",
    "relocation": "Relocation & Work Format",
    "work_format": "Relocation & Work Format",
    "tools": "Tools",
    "interests": "Additional",
}

# Exactly one section may have an open vocabulary, and it has to be this one: a
# language is named whatever it is named, so Languages cannot list its keys in
# advance the way the sections above can. That makes it the only defensible home
# for a key nobody claims — `russian: native` reaches it, and so would a stray key
# from a dissolved section, visibly, where a person can move it. The alternative
# (a catch-all "Additional") sends languages to a section no scoring rule reads.
OPEN_VOCABULARY_SECTION = "Languages"

# Sections the frame declares that are not evidence. A heading outside this set
# and outside KIND_HEADINGS is someone's own, and does not survive a save.
DECLARED_SECTIONS = frozenset(set(KEY_OWNERS.values()) | {
    OPEN_VOCABULARY_SECTION, "Desired Salary", "Skills", "Additional",
})


def section_for_key(key: str) -> str:
    """Where a `key: value` line belongs, wherever it is currently written."""
    return KEY_OWNERS.get(key, OPEN_VOCABULARY_SECTION)


# An evidence item whose kind is missing or outside the vocabulary is employment.
# Not `other`: the catch-all has always meant "an ordinary job we could not label",
# and profiles on disk carry values like "work" that mean exactly that.
DEFAULT_KIND = "employment"


def normalise_kind(raw) -> str:
    """Any stored/model-supplied kind → a kind in the vocabulary."""
    if not raw:
        return DEFAULT_KIND
    kind = str(raw).strip().lower()
    kind = RETIRED_KINDS.get(kind, kind)
    return kind if kind in KIND_HEADINGS else DEFAULT_KIND


def heading_for(kind: str) -> str:
    return KIND_HEADINGS[normalise_kind(kind)]


def kind_for_heading(heading: str) -> str | None:
    """The kind a heading holds, or None when the frame never declared it.

    None is the interesting answer: it means a person wrote this heading, and the
    caller should keep what is under it as `other` rather than guess.
    """
    if heading in HEADING_KINDS:
        return HEADING_KINDS[heading]
    return RETIRED_HEADINGS.get(heading)


def group_by_kind(cases: list) -> dict[str, list]:
    """Cases bucketed by kind, in the frame's own order, empty kinds omitted."""
    buckets: dict[str, list] = {}
    for case in cases or []:
        buckets.setdefault(normalise_kind(case.get("type")), []).append(case)
    return {kind: buckets[kind] for kind in KIND_HEADINGS if kind in buckets}


def evidence_sections(cases: list) -> list[tuple[str, list]]:
    """(heading, cases) in render order — the complete evidence half of a profile.

    `other` is the one kind that does not get a single heading. Its items carry
    the heading a person wrote themselves, in `label`, and go back under it. That
    keeps a hand-organised profile round-tripping unchanged while the structured
    views can still see the items: the heading is data belonging to the item, not
    a slot the frame had to be taught. Reclassifying such an item into a real kind
    is a separate, deliberate act — never a side effect of saving.
    """
    out: list[tuple[str, list]] = []
    for kind, cases_of_kind in group_by_kind(cases).items():
        if kind != "other":
            out.append((KIND_HEADINGS[kind], cases_of_kind))
            continue
        by_label: dict[str, list] = {}
        for case in cases_of_kind:
            by_label.setdefault(str(case.get("label") or KIND_HEADINGS["other"]).strip()
                                or KIND_HEADINGS["other"], []).append(case)
        out += list(by_label.items())
    return out
