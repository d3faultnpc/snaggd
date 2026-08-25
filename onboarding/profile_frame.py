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

import re

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


# ─── The cascade: how deep a record goes, and what each depth MEANS ──────────
#
# Added 2026-08-26. Until now the frame declared the AXES — which sections exist
# (DECLARED_SECTIONS), which kinds of evidence exist (KIND_HEADINGS), which key
# belongs where (KEY_OWNERS), and who may read what (SECTION_READERS). Those held
# for five sprints and are not in question.
#
# What was never declared anywhere is the shape INSIDE a record. `to_md` writes
# one and `md_parse` reads another, and the only thing that kept them agreeing was
# that the same person wrote both on the same day. Every defect found on 2026-08-26
# lives in that gap and none of them live in the axes:
#
#   · a case's own `Context:` overwritten by a first unnamed group's `Context:`
#     — the level of a prose line was inferred from whether a `####` had been seen
#   · `zip(keys, parts)` truncating a five-segment heading in silence, so a live
#     profile's employment period ended up in `domain` and the domain was lost
#   · a record with no name rendering as a bare `###`, because the heading was
#     built from four fields and `label` was not among them
#   · duties read back as ACHIEVEMENTS on `western`, because the heading that
#     separated them was gated on target_market
#   · any body line matching none of the reader's branches dropped without a word
#
# So this block declares the cascade once, for both halves to derive from. The
# rule it encodes came out of the 2026-08-26 measurement over 20 real CVs plus the
# user's own three profiles, and out of the user's own framing: the frame gives
# depth, the source gives categories.


class Level:
    """One depth of the cascade. `optional` is about the LEVEL, not the content:
    an optional level may be absent entirely without the document being wrong."""

    __slots__ = ("depth", "name", "owner", "optional", "means")

    def __init__(self, depth: int, name: str, owner: str, optional: bool, means: str):
        self.depth, self.name, self.owner = depth, name, owner
        self.optional, self.means = optional, means

    def __repr__(self) -> str:  # pragma: no cover — diagnostics only
        return f"<Level h{self.depth} {self.name} owner={self.owner}>"


# `owner` answers "whose words are these": the frame's own vocabulary, or the
# person's document. A level owned by the source must never be given words by us —
# that is how `#### Zone of Responsibility`, a CIS résumé convention, ended up
# baked into the frame and then gated on target_market.
#
# Measured 2026-08-26 over 20 CVs, which is why every level below h2 is optional:
#   employment  92 records — 74 reach h4, 13 stop at prose, 5 stop at the heading
#   education   26 records — 26 stop at the heading. Not a defect: a degree has no
#                            milestones, it has an institution, a name and a year
#   credential  21 records — 18 stop at the heading, 3 at prose, none reach h4
# Forcing one depth on every axis would be exactly the imposed frame we are
# removing. Depth is how much the source said, not what the axis is.
LEVELS = (
    Level(1, "identity", "source", False,
          "who the person is, professionally. One per document."),
    Level(2, "axis", "frame", False,
          "which axis this evidence belongs to. The frame's own vocabulary."),
    Level(3, "record", "source", True,
          "one place, one thing, one time — see RECORD_SLOTS."),
    Level(4, "group", "source", True,
          "a milestone or grouping inside a record, named in the source's words."),
    Level(5, "fact", "source", True,
          "the leaf: a single fact, closest to the ground. Written as a bullet."),
)

LEVEL_BY_DEPTH = {lv.depth: lv for lv in LEVELS}
MAX_DEPTH = max(lv.depth for lv in LEVELS)

# The record's identity, and what each slot MEANS rather than what it is called.
# Naming the meaning is the point: `company` holds a university for `education`
# and an issuer for `credential`, and a reader that knows only the field name has
# to guess. Measured over 138 live cases (2026-08-25): `company` is the place the
# evidence comes FROM, `role` is the thing itself, `period` is when.
# The separator is escaped inside a value, because a value is allowed to contain it
# and the line has to survive being read back. Live: a model returned the role
# "Product Manager | Fintech Platforms (B2B / B2C)" — one field holding the character
# that divides fields. Unescaped, three slots arrived as four parts and the tail was
# lost. Escaping is reversible and stays legible to someone editing the file by hand,
# which sniffing "which part looks like a year" would not be.
_SLOT_SEP = "|"
_SLOT_SPLIT = re.compile(r"(?<!\\)\|")


def _escape_slot(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(_SLOT_SEP, "\\" + _SLOT_SEP)


def _unescape_slot(value: str) -> str:
    return str(value).replace("\\" + _SLOT_SEP, _SLOT_SEP).replace("\\\\", "\\")


RECORD_SLOTS = ("company", "role", "period")
SLOT_MEANING = {"company": "place", "role": "thing", "period": "time"}

# `domain` is deliberately NOT a slot. It rode along as a fourth position until
# 2026-08-26, which is what `zip` truncated: a heading carrying place, thing,
# time AND domain has four parts, and any thing containing a `|` pushed the count
# to five. It belongs under the heading as a declared key, where a name says what
# it is instead of a position implying it.
NON_SLOT_RECORD_KEYS = ("domain", "url")

# Strict writer, tolerant reader. record_name() emits three slots and escapes the
# separator inside a value, so nothing it writes can ever need a fourth. But every
# profile on disk before 2026-08-26 carries `domain` as a fourth POSITION — 19 of
# the 20 corpus files and all three live profiles — and a reader that stopped
# understanding them would not be strict, it would be lossy on the user's own data.
# So reading accepts the legacy fourth; writing has already stopped producing it,
# and a profile drops it on its next save without anyone doing a migration.
READ_SLOTS = RECORD_SLOTS + ("domain",)


def names_place(block: dict) -> bool:
    """Does this block name a place of its OWN, rather than leaning on the record
    above it? `Ранее в компании: …` and `Side project: …` name none — they refer
    to the employer already on the page."""
    return bool(str((block or {}).get("company") or "").strip())


def names_time(block: dict) -> bool:
    """Does this block name a time of its own? A course with only a year names one;
    a progression written `Analyst → Senior → Lead` names none."""
    return bool(str((block or {}).get("period") or "").strip())


def is_record(block: dict) -> bool:
    """The discriminator (2026-08-26, the user's rule, sharpened).

    A block becomes a RECORD (h3) only if it names its own place OR its own time.
    A block naming neither is not an orphan record — it is content of the record
    above it: a named group (h4) if it has a name, prose or a bullet if it does not.

    Why `or` and not `and`: 18 of 21 credentials in the corpus name an issuer and
    no year, and `HTML/CSS. Интерактивный курс | 2020` names a year and no issuer.
    Both are real records. Requiring both would demote them into their neighbours.

    This does not contradict the extraction prompt's rule A ("a separate entry per
    role at the same company") — it says which branch of it applies. Roles carrying
    their own dates each name a time, so each is a record; a bare arrow chain names
    none, so it stays inside the one record it describes.

    It answers exactly one question — record, or content of a record — at exactly
    one boundary, h3. Which axis a record lands under is a different question,
    answered by its kind, and the two must not be merged: merging questions of
    different natures is how duties came to be read as achievements.
    """
    return names_place(block) or names_time(block)


def record_name(block: dict) -> str:
    """The words that go on the h3 line, in the source's own language.

    `label` is last on purpose. It is the name of a thing the frame could not slot
    — for kind `other` it also becomes the SECTION heading (see evidence_sections)
    — and for a record that has a place, the group's name must not displace it.
    Before 2026-08-26 `label` was not consulted here at all, so a project the model
    had named ("AI Health Assistant", present in candidate.json) rendered as a bare
    `###` and lost its name on the way to the file.
    """
    block = block or {}
    parts = [str(block.get(s) or "").strip() for s in RECORD_SLOTS]
    parts = [_escape_slot(p) for p in parts if p]
    if parts:
        return " | ".join(parts)
    return _escape_slot(str(block.get("label") or "").strip())


def parse_record_name(text: str) -> tuple:
    """The inverse of record_name — and the ONLY place a record heading is decoded.

    Returns `(slots, overflow)`. `overflow` is every segment beyond the slots, and
    it is RETURNED rather than dropped: the caller decides what to do with it, but
    it can no longer vanish. `zip(RECORD_SLOTS, parts)` silently discarded it, and
    on live profile `pm` that shifted a whole heading by one slot — the role became
    the company, the period became the role, the domain became the period.

    No shape-sniffing, deliberately. Deciding which segment "looks like a year" is
    the sort of heuristic this codebase has been burned by; the fix is that the
    writer stops putting a fourth thing on this line, not that the reader gets
    cleverer about finding it.
    """
    parts = [_unescape_slot(p.strip()) for p in _SLOT_SPLIT.split(str(text or ""))]
    parts = [p for p in parts if p]
    slots = {slot: value for slot, value in zip(READ_SLOTS, parts) if value}
    return slots, parts[len(READ_SLOTS):]


# ─── h4: what a group inside a record IS ─────────────────────────────────────
#
# Declared 2026-08-26, and the last thing the cascade was missing. h2 has had a
# declared vocabulary since the frame existed (KIND_HEADINGS); h3 has a declared
# shape (RECORD_SLOTS) with the words left to the source. h4 had neither: what a
# group MEANT was carried by whether one specific English string appeared on its
# heading line, and that string was a CIS résumé convention printed into a file
# regardless of whose résumé it was — then gated on target_market, so the same
# bullets were duties for one candidate and achievements for another.
#
# The defect was never that h4 carried a word. `## Work Experience` is the frame's
# word too, printed over a section a Russian CV called «Опыт работы», and nobody
# calls that an imposition — it is a CLASSIFICATION, and a classification has to be
# ours or no consumer can rely on it. What was wrong is that one word stood for two
# kinds, was borrowed from one market, and was assigned by sniffing for a metric
# (extraction rule D) instead of being read off the section the source itself wrote.
#
# So h4 gets the same two-part shape a record already has: a KIND from the frame,
# and a NAME from the source. `#### duties` with `label: Зона ответственности`
# under it — the classification is ours, the words stay theirs.
#
# Two kinds, and the discriminator is the BULLET, not the source's section headings.
# An earlier draft of this file carried a third, `unsorted`, defended on the grounds
# that a western CV often prints one undifferentiated list and sorting it would
# invent a distinction the document never drew. That argument was answered: the
# distinction is not read off the résumé's layout, it is read off each line's own
# nature. An impact carrying a figure is an achievement; a declarative statement of
# what the person did is a responsibility. Nothing is invented, because nothing is
# inferred from where the line happened to sit — so the third kind had no work left.
#
# The same two apply to every axis: employment, education, a driving licence, a side
# project. One shape everywhere, which is what makes a record predictable to read
# without knowing which kind it is.
GROUP_KINDS: tuple = ("responsibilities", "achievement")

# What a group is called when the source drew no line of its own — a hand-edited
# file, or bullets sitting under a record with no `####` above them.
#
# The weaker claim on purpose. Calling something an achievement asserts a RESULT;
# calling it a responsibility asserts only that the person did it. Where the reader
# has to pick without evidence, it must not manufacture accomplishments for people.
DEFAULT_GROUP_KIND = "responsibilities"

# Read tolerantly, write strictly — the same stance the record heading takes. Every
# profile on disk before today carries the literal below (74 of 114 h4 headings in
# the 20-CV corpus); the rest are bare `####`, which is an unnamed achievement group.
# One save converts a file, so this is one legacy form and not a table of them.
RETIRED_GROUP_HEADINGS: dict = {"Zone of Responsibility": "responsibilities"}


def normalise_group_kind(raw) -> str:
    """Anything outside the declared vocabulary becomes the default rather than
    reaching a consumer as an unknown. Same stance as normalise_kind for records."""
    value = str(raw or "").strip().lower()
    return value if value in GROUP_KINDS else DEFAULT_GROUP_KIND


def group_heading(kind: str) -> str:
    """The h4 line for a group of this kind — the frame's word, never the source's."""
    return f"#### {normalise_group_kind(kind)}"


def group_kind_for_heading(text: str) -> str:
    """The kind a `####` line declares, reading legacy files as well as current ones.

    A bare `####` is an unnamed achievement group: that is what it has meant since
    2026-08-17, when the group's name moved off this line into `label:` below it.
    A line carrying anything else is a file written before the name moved, and its
    text is the group's NAME, not its kind — the caller keeps it as `label`.
    """
    label = str(text or "").strip()
    if not label:
        return "achievement"
    if label in RETIRED_GROUP_HEADINGS:
        return RETIRED_GROUP_HEADINGS[label]
    if label.lower() in GROUP_KINDS:
        return label.lower()
    return "achievement"


def groups_of(case: dict) -> list:
    """A record's h4 groups, in render order, read from either shape on disk.

    THE one place that understands the pre-2026-08-26 shape, so nothing else has to.
    A case used to carry two fields for what is one level of the cascade:
    `responsibilities` (a bare list, its kind implied by the CIS heading the renderer
    printed above it) and `highlights` (dicts, kind implied by the ABSENCE of that
    heading). Two encodings of one thing, and the discriminant was an English string
    that a market gate could remove — which is how the same bullets were duties for
    one candidate and achievements for another.

    Returns dicts of {kind, label, context, bullets}: `kind` is the frame's
    classification, `label` is the source's own name for the section and may be None.
    """
    case = case or {}
    declared = case.get("groups")
    if declared:
        out = []
        for group in declared:
            out.append({
                "kind": normalise_group_kind(group.get("kind")),
                "label": group.get("label") or None,
                "context": group.get("context") or None,
                "bullets": [b for b in (group.get("bullets") or []) if str(b).strip()],
            })
        return out

    # Legacy. Duties first, because that is the order the renderer wrote them in and
    # a migration must not reshuffle a person's own document.
    out = []
    if case.get("responsibilities"):
        out.append({"kind": "responsibilities", "label": None, "context": None,
                    "bullets": [b for b in case["responsibilities"] if str(b).strip()]})
    for h in case.get("highlights") or []:
        out.append({"kind": "achievement", "label": h.get("label") or None,
                    "context": h.get("context") or None,
                    "bullets": [b for b in (h.get("results") or []) if str(b).strip()]})
    return out
