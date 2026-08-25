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

# Key-holding sections this renderer used to write and no longer does. Read for the
# keys they hold, then dropped — the keys themselves are placed by KEY_OWNERS like
# any other, so `stop_categories` written under the old heading lands in Constraints
# and `not_looking_for` in Preferences without anything else being said.
#
# `Career Profile` retired 2026-08-25: after the human layer was deleted the name
# described nothing that was still in it. One live profile carried the heading (`pm`),
# which is the whole migration.
RETIRED_KEY_SECTIONS: frozenset = frozenset({"Career Profile"})

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
    # `role_type`, `edge` and `aspiration` lived here until 2026-08-25 and are gone,
    # not moved. They were the human layer — "what I want to be" — and the scoring
    # rebuild took that out of scope deliberately (tz-2026-08-22 §1.3), on a
    # measurement: the rubric named role_type 12 times, aspiration 8 and edge 2,
    # while all three were empty in EVERY profile the CV parser had ever built. The
    # work they were meant to do is done by the document's first heading, which is
    # filled 8 times out of 8 and which the scoring prompt names as the comparison
    # frame. Deleting rather than keeping: a field nobody reads is a field that will
    # be read by accident.
    #
    # What is left is not a profile — it is two kinds of refusal, and they are not
    # the same kind, so they are not in the same section.
    #
    # A hard one. The semantic categories and named employers the person refuses
    # outright. It has to live in THIS file, because the model reads it here and the
    # block validator checks an answer against the same line — two copies in two
    # files is how they drift. Addressed to nobody by SECTION_READERS: the scorer is
    # handed the list explicitly by llm_agent (a vocabulary an answer is validated
    # against is data the call needs, not a side effect of projection), and no letter
    # or form answer has any business volunteering a refusal to an employer.
    "stop_categories": "Constraints",
    # A soft one, and deliberately without a mechanism (user's decision 2026-08-25):
    # "не хочу его объявлять жёстко, пусть будет мягким, в свободной форме". It
    # filters nothing and blocks nothing. It reaches the letter writer, which can use
    # it to avoid pitching someone into work they said they did not want, and it does
    # NOT reach the answerer for the same reason stop_categories does not: a refusal
    # is not something to volunteer into a form addressed to an employer.
    "not_looking_for": "Preferences",
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


# Keys the frame used to own and dropped, rather than moved. Named rather than
# forgotten, because "undeclared" and "retired" are not the same thing and must not
# be treated the same: an UNDECLARED key is someone's own and gets dissolved into the
# open-vocabulary section so a person can see it and move it; a RETIRED key is one WE
# removed, and dissolving it would file `role_type: hands-on builder` under
# `## Languages` on the next save of every profile that still carries it.
#
# role_type / edge / aspiration retired 2026-08-25 with the human layer (see
# KEY_OWNERS). A profile carrying them loses them on its next save — that is the
# migration, and it is visible: the save is backed up like any other.
#
# This set is expected to shrink to nothing, and should be deleted when it does. A
# retirement nobody retires is a rule with no cases — the same reasoning that removed
# RETIRED_KINDS on 2026-08-24 once every profile had been re-parsed.
RETIRED_KEYS: frozenset = frozenset({"role_type", "edge", "aspiration"})


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
    # Two readerships, not three (2026-08-25, user's rule: "писатель и отвечала
    # видят всё что объявлено... притом скорер не видит зарплату, это не входит в
    # задачи скоринга").
    #
    # THE SCORER sees evidence and capability, and nothing else. That is the whole
    # point of the projection: judging whether a person can do a job while holding
    # their salary range and relocation preference is the coupling this rebuild
    # removed. Note it does not need Identity either — the document's first heading
    # is the person's professional identity and rides above the first `##`, kept for
    # every reader by project_for.
    #
    # THE LETTER AND THE FORM ANSWER see everything about the person, because both
    # write TO an employer on their behalf and an employer can ask about any of it.
    # Until today the map said otherwise by omission, and nobody had noticed because
    # the flag that enforces it is off: the letter writer was not given the stack,
    # the languages, or the NAME of the person it writes for. Same class of defect as
    # Languages being withheld from the scorer, which was found by measurement.
    #
    # The only two exceptions are refusals, and both are refusals for the same
    # reason: what a person will not do is not something to volunteer to an employer.
    "Constraints": (),
    # Both, and the reason the first draft of this map said "cover" only is worth
    # keeping: the split was drawn along CALL TYPE when it belongs along the nature
    # of the text. `fill_form` is a single call that answers short fields AND writes
    # the cover letter when the form has one glued among them (see prompts/form_fill.md,
    # "Cover letter / motivation letter fields"). Giving this to `cover` alone meant a
    # standalone letter avoided work the person said they did not want, and the same
    # letter inside a form did not — one behaviour decided by which surface the
    # employer happened to use.
    #
    # Not volunteering a refusal to an employer is still the rule. It is a rule about
    # what to WRITE, so it lives in the prompt that writes; a projection can only
    # decide what is known, and withholding it here bought a blind spot instead.
    "Preferences": ("cover", "answer"),

    "Skills": ("score", "cover", "answer"),
    # The stack, when a posting asks for one by name. Undeclared until 2026-08-22,
    # which is the likeliest reason it behaved unpredictably: present in every
    # prompt, named by none of them.
    "Tools": ("score", "cover", "answer"),
    # The scorer reads these because a language IS a requirement a posting states by
    # name — for a translator, a salesperson or a hotel receptionist it is THE
    # requirement — and it sits on the same axis as any other skill. Withholding it
    # was the sharpest single defect in this map when it was written: 11 of 13 live
    # profiles carry languages and the scoring prompt named them zero times.
    "Languages": ("score", "cover", "answer"),

    # Not evidence about capability, and exactly what an employer's form asks for.
    # The letter gets them too — it signs with a name and can answer a stated
    # condition in prose rather than leaving it for a form.
    "Identity": ("cover", "answer"),
    "Desired Salary": ("cover", "answer"),
    "Relocation & Work Format": ("cover", "answer"),
    # `interests` — a letter can use one, a score cannot.
    "Additional": ("cover", "answer"),
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
