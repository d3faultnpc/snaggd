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

# RETIRED_KINDS lived here until 2026-08-24: a translation table for two kinds the
# vocabulary once had (`certification` → `credential`, `research` → `project`). It
# was a migration artifact and it was meant to shrink to nothing, which it now has —
# every profile on disk has been re-parsed onto the current vocabulary, and a kind
# outside it becomes `other` like any other invention. Removed rather than kept
# "just in case": a translation nobody translates is a rule with no cases, and it
# would quietly accept a retired name back into the vocabulary if a model produced one.

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


# Who each section is FOR. The frame has always said which sections exist; it has
# never said which of them any particular call has business reading, and the whole
# profile went into the system prompt of every call as one undivided block.
#
# That is not tidiness. Scoring is "this position against this person": a salary
# range and a relocation preference say nothing about whether they can do the job,
# and they were sitting in the scorer's context anyway, influencing by presence
# with no rule attached. The answerer has the opposite need — an employer's
# question arrives unannounced ("desired salary?", "willing to relocate?"), and
# anything missing is a question it cannot answer.
#
# Declared here and not yet enforced: this map is the vocabulary, and projecting
# each call onto its own slice is a separate change with its own measurements.
# Declaring it first is what makes that change reviewable instead of a rewrite.
SECTION_READERS = {
    # Addressed to nobody, for now, and that is a deliberate empty tuple rather
    # than an omission (see project_for: undeclared is kept, declared-empty is
    # not). The section holds five keys of four different natures — role_type and
    # edge and aspiration are what the person wants, not_looking_for is a soft
    # refusal, stop_categories is a hard one — and the first four are out of scope
    # while the scorer is rebuilt on evidence alone. Measured reason: across 13
    # live profiles role_type appears in 4 and aspiration in 2, and neither
    # appears in ANY of the 8 built by the CV parser, while the scoring prompt
    # named them 12 and 8 times. It was a rubric for one hand-written profile.
    #
    # stop_categories used to reach the model by riding along in here. It no
    # longer depends on a section surviving projection: llm_agent passes the
    # declared categories into the scoring call explicitly, because a vocabulary
    # the answer is checked against is data the call needs, not a side effect of
    # what the projection happened to keep.
    "Career Profile": (),
    # The answerer needs these too: "list your key skills" is one of the most
    # ordinary things an employer's form asks, and a projection that withheld
    # them would leave it with nothing to answer from. Career Profile is
    # deliberately NOT extended the same way — it carries not_looking_for and
    # stop_categories, and a refusal is not something to volunteer into a form
    # answer addressed to an employer.
    "Skills": ("score", "cover", "answer"),
    # `interests` — a letter can use one, a score cannot. The scoring prompt used
    # to carry a clause about low-confidence notes here needing a case to support
    # them; that clause is gone with the domain modifier it qualified, and text
    # reaching the model with no rule attached is the thing this map exists to
    # stop, not something it should keep doing quietly.
    "Additional": ("cover",),
    # The stack, when a posting asks for one by name. Undeclared until now, which
    # is the likeliest reason it behaved unpredictably: present in every prompt,
    # named by none of them.
    "Tools": ("score", "answer"),
    # Answerer only, all four. None of them is evidence about capability, and
    # each is exactly what an employer's form asks for.
    "Desired Salary": ("answer",),
    "Relocation & Work Format": ("answer",),
    # The scorer reads these because a language IS a requirement a posting states
    # by name — for a translator, a salesperson or a hotel receptionist it is THE
    # requirement — and it sits on the same axis as any other skill. Withholding
    # it was the sharpest single defect in this map: 11 of 13 live profiles carry
    # languages, and the scoring prompt named them zero times.
    "Languages": ("score", "answer"),
    "Identity": ("answer",),
}

# The call types a reader may name. "answer" covers both form filling and the
# live HR chat: they differ in where the answer is typed, not in what they are
# allowed to know about the person.
READER_KINDS = frozenset({"score", "cover", "answer"})


# Evidence is one list with a discriminant, so its readers are declared once
# rather than eight times. Every kind of case — a job, a degree, a certificate, a
# side project — is the person's own record of what they have done, and all three
# kinds of call have business reading it: the scorer to match it, the letter to
# quote it, the answerer because an employer can ask about any of it.
EVIDENCE_READERS = ("score", "cover", "answer")


def readers_of(section: str) -> tuple:
    """Which kinds of call this section is addressed to. Empty tuple = nobody's."""
    if section in HEADING_KINDS:
        return EVIDENCE_READERS
    return SECTION_READERS.get(section, ())


def project_for(markdown: str, reader: str) -> str:
    """The parts of a profile addressed to one kind of call, verbatim.

    Splits on `## ` only. A case is a `### ` under its section and a person's own
    `#### ` sits inside that, so cutting at level two keeps every case whole —
    and the text of each kept section is passed through untouched, because this
    is the person's own file and a projection has no business reformatting it.

    A heading the frame does not know is kept for everyone. Removing something
    requires knowing it is addressed elsewhere; not recognising it is not knowing
    that. It also means a document the frame has never seen — a future second
    profile file, or a person's own section — passes through rather than being
    silently dropped.

    The preamble above the first `## ` — the person's own headline — always
    stays: it is who they are, not a rubric.
    """
    if not markdown:
        return markdown
    kept, current, keep_current = [], [], True
    for line in markdown.splitlines():
        if line.startswith("## "):
            if keep_current:
                kept.extend(current)
            heading = line[3:].strip()
            known = SECTION_READERS.get(heading)
            if heading in HEADING_KINDS:
                known = EVIDENCE_READERS
            # Three states, not two. Undeclared (None) is kept for everyone —
            # removing something requires knowing it is addressed elsewhere, and not
            # recognising a heading is not knowing that. Declared with no readers is
            # addressed to NOBODY and goes. readers_of's own docstring has said
            # "empty tuple = nobody's" since it was written, while this line read an
            # empty tuple as ignorance and kept the section for every caller — a rule
            # with nobody to enforce it.
            keep_current = True if known is None else (reader in known)
            current = [line]
        else:
            current.append(line)
    if keep_current:
        kept.extend(current)
    return "\n".join(kept).strip()


# An evidence item whose kind is missing or outside the vocabulary is employment.
# Not `other`: the catch-all has always meant "an ordinary job we could not label",
# and profiles on disk carry values like "work" that mean exactly that.
DEFAULT_KIND = "employment"


def normalise_kind(raw) -> str:
    """Any stored/model-supplied kind → a kind in the vocabulary."""
    if not raw:
        return DEFAULT_KIND
    kind = str(raw).strip().lower()
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
