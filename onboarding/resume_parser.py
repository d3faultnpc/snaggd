"""
Resume parser: PDF / DOCX / image / markdown → ResumeData → candidate.md + candidate.json

- PDF + images → base64 image_url → Gemini reads both natively (no local extraction)
- DOCX → python-docx text → LLM text mode (no image representation available)
- MD/TXT → LLM text mode
- json_repair as fallback for malformed LLM JSON output
- OpenRouter as unified gateway (RESUME_PARSE_MODEL / LLM_MODEL env vars)

Schema: see .claude/working-notes/tz-pre-app-wizard-sprint.md Task 1. ResumeData mirrors the
candidate.json shape directly (nested dicts/lists) so dataclasses.asdict() round-trips cleanly.
"""

import base64
import copy
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from json_repair import repair_json
    _HAS_JSON_REPAIR = True
except ImportError:
    _HAS_JSON_REPAIR = False

from onboarding import md_merge
from onboarding.profile_frame import evidence_sections, normalise_kind

# Output ceiling for one CV extraction. Raised from 2500 on 2026-08-16: a rich
# resume — seven kinds of evidence, each case carrying its own prose — ran past
# it, and a reply cut off at the ceiling is not malformed, just short. json_repair
# then closed the stump into valid JSON, so the profile simply arrived with its
# last cases missing and nothing anywhere said so.
#
# A ceiling is a cap, never a target: a model that finishes in 900 tokens still
# finishes in 900 and is billed for 900. Raising this costs nothing on the
# answers that were already fitting; it only stops charging the ones that were
# not fitting a silent amputation.
#
# Truncation past this stays non-fatal by decision (2026-08-16): the user gets
# the profile that was read rather than an error posing a problem they cannot
# act on. It is recorded at the gateway (core.llm_agent._note_call), so how
# often it still happens is a question with an answer.
_PARSE_MAX_TOKENS = 5000

_TOKEN_GUARD_CHARS = 6000


@dataclass
class ResumeData:
    schema_version: str = "1.0"
    target_market: str = ""
    locale: str = ""

    identity: dict = field(default_factory=dict)        # name, role, location, contacts: []
    pitch: Optional[str] = None

    # career_profile.role_type/edge are the candidate's own confirmed framing, set via the
    # wizard (possibly starting from career_profile_suggestions below, but never written
    # directly by the parser) — logistics/search/rules are filter/config data, not CV content.
    career_profile: dict = field(default_factory=dict)  # role_type, edge
    logistics: dict = field(default_factory=dict)       # relocation, work_format
    search: dict = field(default_factory=dict)          # wise_link, queries, salary, region
    rules: dict = field(default_factory=dict)           # stop, penalize, min_match, min_employer_rating

    cases: list = field(default_factory=list)
    skills: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    languages: list = field(default_factory=list)       # [{lang, level, note}]
    interests: list = field(default_factory=list)

    # Parser-only convenience field (HH search query suggestions) — NOT part of the
    # candidate.json schema, excluded when serializing. Feeds the existing job_preferences.md
    # search-direction flow (wizard.py Block B), unrelated to search{} above.
    suggested_queries: list = field(default_factory=list)

    # Parser-only convenience field, same exclusion treatment as suggested_queries above —
    # 0-3 LLM-suggested role_type quick-picks for the wizard's SegmentFreetextField to render
    # as buttons alongside its free-text input. Never the final career_profile.role_type value
    # itself, and never forced — empty when the CV doesn't clearly support one.
    career_profile_suggestions: dict = field(default_factory=dict)  # role_type_options: []

    # Operational metadata — not schema content, used directly by Python code
    source_file: str = ""
    parsed_at: str = ""
    hints: list = field(default_factory=list)


def _ensure_https(url: str) -> str:
    """Bare domains (github.com/x, t.me/x — common LLM-extraction output, protocol not
    guaranteed) don't auto-link in most MD viewers. @handles, non-URL values, and values that
    already have any URI scheme (including slashless ones like mailto:/tel:) pass through."""
    if url and not url.startswith("@") and not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.IGNORECASE):
        return f"https://{url}"
    return url


# A contact is asked for bare and often comes back wearing its own label —
# "Telegram: @x", "Email: x@y". Sniffing that string and then adding a label of
# our own wrote `telegram: Telegram: @x` into the file, and the ones that missed
# every branch (the space after the colon is enough to miss the email branch)
# went through untouched, so `Email: x@y` became a line keyed `Email` — a key the
# frame does not own, which `section_for_key` therefore files under Languages and
# `md_parse` reads as part of the person's pitch. One label, ours, on a value
# that carries none.
_KNOWN_CONTACT_LABEL_RE = re.compile(
    r"^(?:telegram|tg|e-?mail|mail|phone|tel|mobile|cell|github|linkedin|contact)"
    r"\s*[:\-–—]\s*",
    re.IGNORECASE,
)

# Any other word used as a label. Listing labels by name would only ever cover
# the ones already seen — and the damage does not depend on which word it is,
# only on a colon surviving into the file. What separates a label from a URI
# scheme is the whitespace after the colon: `https://t.me/x` and `mailto:x` have
# none and must come through untouched.
_ANY_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,20}:\s+")


def _strip_contact_label(raw: str) -> str:
    """Drop a label the source already wrote in front of the value."""
    prev = None
    out = raw.strip()
    # "Contact: Telegram: @x" is one line wearing two labels. The loop terminates
    # because every pass that changes the string also shortens it.
    while out != prev:
        prev = out
        out = _KNOWN_CONTACT_LABEL_RE.sub("", out, count=1)
        out = _ANY_LABEL_RE.sub("", out, count=1).strip()
    return out


def _typed_contact_line(raw: str) -> str:
    """Type-sniff a raw contact string into a labeled line for MD rendering.

    Whatever fails every branch is returned genuinely unlabeled, which is what
    `md_parse` documents this function as doing: a bare single token reads back
    as a contact, and only real prose falls through to the pitch.
    """
    raw = _strip_contact_label(raw)
    low = raw.lower()
    if "t.me/" in low or raw.startswith("@"):
        return f"telegram: {_ensure_https(raw)}"
    if "github.com" in low:
        return f"github: {_ensure_https(raw)}"
    if "linkedin.com" in low:
        return f"linkedin: {_ensure_https(raw)}"
    if "@" in raw and " " not in raw:
        return f"email: {raw}"
    digits = sum(ch.isdigit() for ch in raw)
    if digits >= 7:
        return f"phone: {raw}"
    return raw


class ResumeParser:
    SUPPORTED_TYPES = {
        ".pdf":  "application/pdf",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".md":   "text/markdown",
        ".txt":  "text/plain",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    # Gemini Flash reads PDF and images natively via image_url. gemini-2.0-flash-001
    # was discontinued 2026-07 (confirmed live) — 2.5-flash is the current default,
    # same family, same native-PDF-reading rationale. RESUME_PARSE_MODEL still
    # overrides for anyone who wants a different multimodal model.
    MULTIMODAL_MODEL = os.getenv("RESUME_PARSE_MODEL", "google/gemini-2.5-flash")
    # claude-3-5-haiku 404s live as of 2026-07 — claude-haiku-4.5 is the current
    # same-tier replacement. LLM_MODEL still overrides as before.
    TEXT_MODEL       = os.getenv("LLM_MODEL", "anthropic/claude-haiku-4.5")

    def __init__(self, llm_client):
        self.llm = llm_client

    # ── Public API ────────────────────────────────────────────────────────────

    def parse_file(self, path: Path) -> ResumeData:
        path = Path(path)
        ext = path.suffix.lower()
        if ext not in self.SUPPORTED_TYPES:
            raise ValueError(f"Unsupported file type: {ext}. Supported: {list(self.SUPPORTED_TYPES)}")

        mime = self.SUPPORTED_TYPES[ext]

        if mime in ("text/markdown", "text/plain"):
            return self._extract_with_llm(path.read_text(encoding="utf-8"), path.name)

        if ext == ".docx":
            # DOCX has no image representation — extract text, send as text
            return self._extract_with_llm(self._extract_docx_text(path), path.name)

        # PDF and images: always multimodal — Gemini reads layout/structure natively
        return self._extract_multimodal(path, mime)

    def from_wizard(self, answers: dict) -> ResumeData:
        """Manual-entry fallback (no LLM_API_KEY / parse failure). Minimal by design —
        full per-case manual entry is Task 6 (wizard 7-step redesign), not this."""
        data = ResumeData(
            identity={
                "name": answers.get("name"),
                "role": answers.get("role"),
                "location": answers.get("location"),
                "contacts": answers.get("contacts") or [],
            },
            skills=answers.get("skills") or [],
            tools=answers.get("tools") or [],
            languages=answers.get("languages") or [],
            cases=answers.get("cases") or [],
            source_file="wizard",
            parsed_at=datetime.now().isoformat(),
        )
        return self._finalize(data)

    def to_md(self, data: ResumeData, existing_content: str = "") -> str:
        """Serialize ResumeData → candidate.md (dense format optimized for LLM tokens).

        existing_content: previous file content, if any. Merged section by section —
        what this renderer emits wins, what it does not emit survives. candidate.md
        holds preferences the schema has no field for (see onboarding/md_merge.py for
        the real profile that made this necessary), and those must outlive a save.

        Evidence is the exception, and is resolved before rendering — see
        _carry_unclaimed_evidence. Section-by-section is the wrong unit for it.
        """
        data = self._carry_unclaimed_evidence(data, existing_content)
        body = self._render_managed_block(data)
        if len(body) > _TOKEN_GUARD_CHARS:
            print(f"⚠️  candidate.md managed block exceeds {_TOKEN_GUARD_CHARS} chars — shortening", file=sys.stderr)
            body = self._render_managed_block(self._shorten_for_token_guard(data))

        return md_merge.merge(existing_content or "", body)

    @staticmethod
    def _carry_unclaimed_evidence(data: ResumeData, existing_content: str) -> ResumeData:
        """Evidence is owned by kind, not by section.

        The contract a person signs by re-running the wizard, in their words: a CV
        overwrites what was already known and adds what was not. Applying that per
        section cannot work, because the same two real projects can sit in the file
        under one heading and arrive from a CV under another — and then "add what
        was not known" writes them a second time. It happened live: one profile
        ended up asserting `english: B2` and `english: C1` at once.

        Per kind, it works and needs no identity for individual items — which is
        just as well, since a person rewording their own case history is the normal
        thing and no key survives it:

            a save that states evidence of kind K replaces all of kind K
            a save that says nothing about kind K leaves kind K alone
            a save with no evidence at all changes no evidence at all

        `other` is deliberately not protected by the second rule. It is not a kind;
        it is the mark of a classification that did not happen, and a CV parser
        never emits it — so "says nothing about `other`" is true of every save
        forever, and anything landing there would be trapped for good. Treating it
        as superseded by any real evidence keeps the one-way door from existing.
        How often it appears at all is the measure of whether the frame's
        vocabulary fits real CVs.
        """
        if not data.cases or not existing_content:
            return data
        from onboarding.md_parse import parse_candidate_md

        claimed = {normalise_kind(c.get("type")) for c in data.cases}
        carried = [c for c in parse_candidate_md(existing_content).get("cases", [])
                   if normalise_kind(c.get("type")) not in claimed
                   and normalise_kind(c.get("type")) != "other"]
        if not carried:
            return data
        merged = copy.copy(data)
        merged.cases = list(data.cases) + carried
        return merged

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render_managed_block(self, data: ResumeData) -> str:
        """Only what this data actually carries — never a placeholder for what it does not.

        Placeholders used to be written here (`# HINT: add your city`, `# SKIPPABLE`,
        `MISSING — add your name`). candidate.md goes into the system prompt verbatim,
        so those instructions-to-the-user were read by the model as candidate facts —
        visibly enough that prompts/match_scoring.md had to grow a clause telling it to
        ignore the word "SKIPPABLE". Guidance belongs in the wizard UI, where a person
        reads it. Here, absent means absent, and md_merge relies on exactly that: a
        section this renderer omits is a section the wizard has nothing to say about,
        so whatever is already in the file stays.
        """
        identity = data.identity or {}
        lines = []
        if identity.get("role"):
            lines.append(f"# {identity['role']}")

        # ── Identity ───────────────────────────────────────────────────────
        identity_lines = []
        if identity.get("name"):
            identity_lines.append(f"name: {identity['name']}")
        if identity.get("location"):
            identity_lines.append(f"location: {identity['location']}")
        for c in identity.get("contacts") or []:
            identity_lines.append(_typed_contact_line(c))
        if data.pitch:
            identity_lines.append(data.pitch)
        if identity_lines:
            lines += ["", "## Identity"] + identity_lines

        # ── Career Profile ────────────────────────────────────────────────
        cp = data.career_profile or {}
        career_lines = [f"{k}: {cp[k]}" for k in ("role_type", "edge", "aspiration") if cp.get(k)]
        # `not_looking_for` is where "soft-skip anything with X" actually works, and it has
        # worked for a long time — as prose in a hand-written candidate.md that the model
        # reads. There is no numeric penalty behind it anywhere in the engine, so the wizard
        # collected `rules.penalize` into candidate.json and nothing ever read it. Writing it
        # here connects the field to the mechanism that already existed.
        penalize = [str(p).strip() for p in ((data.rules or {}).get("penalize") or []) if str(p).strip()]
        if penalize:
            career_lines.append(f"not_looking_for: {', '.join(penalize)}")
        # Semantic categories the person refuses outright — the hard tier of the same
        # preference `not_looking_for` states softly. Rendered here so it lands in the
        # one file the model already reads, which is also the list the block validator
        # checks an answer against: one line, one meaning, no second copy to drift.
        stop_categories = [str(c).strip() for c in ((data.rules or {}).get("stop_categories") or [])
                           if str(c).strip()]
        if stop_categories:
            career_lines.append(f"stop_categories: {', '.join(stop_categories)}")
        if career_lines:
            lines += ["", "## Career Profile"] + career_lines

        # ── Relocation & Work Format ─────────────────────────────────────
        lg = data.logistics or {}
        logistics_lines = [f"{k}: {lg[k]}" for k in ("relocation", "work_format") if lg.get(k)]
        if logistics_lines:
            lines += ["", "## Relocation & Work Format"] + logistics_lines

        # No target-roles section here, deliberately (decided 2026-08-14). candidate.md is
        # the one file the scorer reads to answer "how well does this vacancy fit THIS
        # person", and it must describe the person only. A list of roles being hunted is a
        # description of the wanted vacancy, and putting it in the same file hands the model
        # a second thing to compare against — it can start scoring the sought role against
        # the resume instead of the actual vacancy against the resume. Conditions the person
        # wants (salary, work format, relocation) are candidate attributes and belong here;
        # the job title being chased is not one. The vacancy feed comes from the HH wise
        # link, which is where "what am I looking for" is expressed for now.

        # ── Desired Salary ────────────────────────────────────────────────
        salary = (data.search or {}).get("salary")
        if salary:
            lines += ["", "## Desired Salary", salary]

        # ── Skills ────────────────────────────────────────────────────────
        if data.skills:
            # One shape for both lists of the same nature. Skills used to be
            # bullets while Tools, the section directly below it, was a single
            # `tools:` line — so the file taught two grammars for one kind of
            # thing, and a person editing Skills by hand wrote the other one.
            #
            # The keyed line wins on all three counts that were weighed: it costs
            # the same to read (18 real skills — 354 characters as bullets, 343 on
            # one line, so nothing is paid for the compactness), it takes 2 lines
            # instead of 19 in a pane a person scrolls, and every character in it
            # is one they can type. A separator they cannot reach on a keyboard
            # would make the file's own format something they can read and not
            # write, which is the opposite of why this file is editable.
            lines += ["", "## Skills", "skills: " + ", ".join(data.skills)]

        # ── Evidence, one section per kind that has anything in it ────────
        # One list in the schema, grouped here. Three headings used to hold seven
        # kinds, so a certificate and a diploma came back from the file identical
        # and a hand-written heading matched none of them. The frame declares both
        # the kinds and their headings — see onboarding/profile_frame.py.
        for heading, cases in evidence_sections(data.cases):
            lines += ["", f"## {heading}"]
            for case in cases:
                # A zone-of-responsibility block is something employment has. A
                # certificate or a talk has no ongoing duties.
                lines += self._render_case(
                    case, data.target_market,
                    include_zone=normalise_kind(case.get("type")) == "employment")

        # ── Tools ─────────────────────────────────────────────────────────
        # `tools:` rather than a bare line: a key is what lets this content be
        # placed when it turns up in a section someone invented (profile_frame's
        # KEY_OWNERS). It also makes the section merge per key like its neighbours.
        if data.tools:
            lines += ["", "## Tools", "tools: " + ", ".join(data.tools)]

        # ── Languages ─────────────────────────────────────────────────────
        if data.languages:
            lines += ["", "## Languages"]
            for lang in data.languages:
                note = f" ({lang['note']})" if lang.get("note") else ""
                lines.append(f"{lang.get('lang', '')}: {lang.get('level', '')}{note}")

        # ── Additional ────────────────────────────────────────────────────
        additional_lines = []
        if data.interests:
            additional_lines.append(f"interests: {', '.join(data.interests)}")
        if data.hints:
            additional_lines.append("hints (low-confidence — verify before relying on):")
            additional_lines += [f"- {h}" for h in data.hints]
        if additional_lines:
            lines += ["", "## Additional"] + additional_lines

        return "\n".join(lines).lstrip("\n")

    def _render_case(self, case: dict, target_market: str, include_zone: bool) -> list:
        header_parts = [x for x in [case.get("company"), case.get("role"),
                                     case.get("period"), case.get("domain")] if x]
        # A bare "###" when nothing names this entry. The heading is a boundary
        # md_parse reads; its text is content, and content that does not exist is
        # not written. It used to print "MISSING — company/role/period", which
        # went verbatim into the system prompt — an instruction for a person,
        # read by the model as a fact about the candidate.
        lines = ["", f"### {' | '.join(header_parts)}".rstrip()]

        if case.get("url"):
            lines.append(f"url: {_ensure_https(case['url'])}")

        highlights = case.get("highlights") or []
        responsibilities = case.get("responsibilities") or []

        # Rendered whenever it exists, not only for a case with nothing else. The old
        # condition silently dropped the prose of any case that also had bullets, which
        # is the ordinary shape of a hand-written project entry.
        ctx = case.get("context")
        if ctx:
            lines.append(f"Context: {ctx}")
        for key, value in (case.get("notes") or {}).items():
            lines.append(f"{key}: {value}")

        # The HEADING is an employment convention — an award or a certificate has
        # no ongoing duties, so it gets no "Zone of Responsibility" line. The
        # CONTENT is not a convention; it is what the person typed.
        #
        # Until 2026-08-18 the two were gated together, so every kind except
        # employment lost its bullets on save — silently, with no error and no
        # re-homing. The wizard's tag editor writes into `responsibilities` for
        # EVERY card regardless of kind (see WizardOverlay's setField), so this
        # was not a rare shape: found live on an Awards entry whose six bullets
        # sat in candidate.json and in the mirror, and never reached the file.
        #
        # Unlabelled bullets straight under the entry are already how an unnamed
        # group is written here, and md_parse reads them back as one — so the
        # content survives through a path that already exists, and the file grows
        # no new shape. Round trip pinned in tests/test_candidate_md_roundtrip.py.
        wrote_leading_group = False
        if responsibilities and target_market != "western" and target_market != "global":
            if include_zone:
                lines += ["", "#### Zone of Responsibility"]
            lines += [f"- {r}" for r in responsibilities]
            wrote_leading_group = True

        # A group's boundary and a group's name are two different things, and
        # writing them as one line made the name load-bearing: a group only got a
        # boundary when the model had named it, so two unnamed groups came back as
        # one (reproduced: 2 in, 1 out). And the name, invented by the model, was
        # a heading in a person's own file — the same "string IS the slot" the
        # frame removed from `##` and never removed from here.
        #
        # So: `####` is the boundary and carries nothing; `label:` is the name,
        # written as content among content, next to `Context:` and `url:`. The
        # first group needs no separator — bullets straight under the entry
        # already read back as one unnamed group — but a named one does, because
        # its `label:` has to attach to a group rather than to the entry.
        for index, h in enumerate(highlights):
            label, h_ctx, results = h.get("label"), h.get("context"), (h.get("results") or [])
            # `wrote_leading_group` matters for the same reason `index > 0` does:
            # the bullets above already occupy the entry's first unnamed group, so
            # a first highlight without a label needs a boundary or the two read
            # back as one group and the next save writes them merged.
            if index > 0 or label or wrote_leading_group:
                lines += ["", "####"]
            if label:
                lines.append(f"label: {label}")
            if h_ctx:
                lines.append(f"Context: {h_ctx}")
            lines += [f"- {r}" for r in results]

        return lines

    def _shorten_for_token_guard(self, data: ResumeData) -> ResumeData:
        import copy
        trimmed = copy.deepcopy(data)
        for case in trimmed.cases:
            for h in (case.get("highlights") or []):
                ctx = h.get("context")
                if ctx and ". " in ctx:
                    h["context"] = ctx.split(". ")[0].strip().rstrip(".") + "."
            if case.get("responsibilities"):
                case["responsibilities"] = case["responsibilities"][:3]
        return trimmed

    # ── Extraction methods ────────────────────────────────────────────────────

    def _extract_docx_text(self, path: Path) -> str:
        try:
            from docx import Document
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            return ""

    def _extract_multimodal(self, path: Path, mime: str) -> ResumeData:
        """Send file as base64 image_url — works for images and scanned PDFs (Gemini)."""
        b64 = base64.b64encode(path.read_bytes()).decode()
        response = self.llm.chat.completions.create(
            model=self.MULTIMODAL_MODEL,
            max_tokens=_PARSE_MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": self._extraction_prompt()},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
        )
        raw = response.choices[0].message.content or "{}"
        return self._parse_json_response(raw, source_file=path.name)

    def _extract_with_llm(self, text: str, source_file: str) -> ResumeData:
        response = self.llm.chat.completions.create(
            model=self.TEXT_MODEL,
            max_tokens=_PARSE_MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": f"{self._extraction_prompt()}\n\nCV text:\n{text}",
            }],
        )
        raw = response.choices[0].message.content or "{}"
        return self._parse_json_response(raw, source_file=source_file)

    # ── Prompt & parsing ──────────────────────────────────────────────────────

    def _extraction_prompt(self) -> str:
        return (
            "Extract structured information from this CV/resume.\n\n"
            "Return ONLY valid JSON, no markdown, no wrapper:\n"
            "{\n"
            '  "target_market": "cis | western | global",\n'
            '  "locale": "ru | en",\n'
            '  "identity": {\n'
            '    "name": "Full name or null",\n'
            '    "role": "Short profession/role label only — 2-4 words, no platform/tech/company '
            'detail (e.g. \'fintech PM\', \'barista\', \'dentist\') or null",\n'
            '    "location": "City or null",\n'
            '    "contacts": ["raw contact strings — URLs, @handles, emails, phone numbers, exactly as found"]\n'
            '  },\n'
            '  "pitch": "1-2 sentence narrative summary/elevator pitch, only if the CV has one, else null",\n'
            '  "cases": [\n'
            '    {\n'
            '      "type": "EXACTLY ONE OF: employment | education | credential | project | '
            'publication | volunteering | award | other. These eight and no others — a value '
            'outside this list is discarded and the entry becomes other. credential covers '
            'certificates, licences and courses. Use other only when none of the seven fits, '
            'and then `label` is required",\n'
            '      "label": "required for type=other, null otherwise — what this part of the CV '
            'is called, in the CV\'s own words",\n'
            '      "company": "Company / institution / project name",\n'
            '      "role": "Job title / degree / project role",\n'
            '      "period": "2022–2024",\n'
            '      "domain": "industry domain — employment cases only",\n'
            '      "context": "1-2 sentences, used only when there is no highlight/responsibility to attach it to",\n'
            '      "url": "URL if present, else null",\n'
            '      "responsibilities": ["ongoing duty bullets with no single crisp metric"],\n'
            '      "highlights": [\n'
            '        {"label": "project/initiative name or null", "context": "1-2 sentences", '
            '"results": ["quantified metric tied to this highlight"]}\n'
            '      ]\n'
            '    }\n'
            '  ],\n'
            '  "skills": ["skill1", "skill2"],\n'
            '  "tools": ["tool1", "tool2"],\n'
            '  "languages": [{"lang": "english", "level": "B2", "note": null}],\n'
            '  "interests": ["interest1"],\n'
            '  "career_profile_suggestions": {\n'
            '    "role_type_options": ["0-3 short archetype labels (2-4 words each, e.g. \'hands-on '
            'builder\', \'process-driven operator\', \'high-volume service lead\') ONLY if the CV\'s '
            'cases clearly support one — empty array if nothing clearly points that way, never force '
            'a guess"]\n'
            '  },\n'
            '  "hints": ["content that does not clearly fit one bucket above — low-confidence, do not force a classification"],\n'
            '  "suggested_queries": ["product manager b2b", "руководитель продукта"]\n'
            "}\n\n"
            "Rules:\n"
            "A — Multi-role: if the candidate held multiple positions at the same company, create a "
            "separate case entry per role, each with its own role/period/highlights. Do not merge roles "
            "into one entry.\n"
            "B — Bullet split: if a bullet has a project/initiative name followed by metrics, put the "
            "name in highlights[].label and the metrics as separate strings in highlights[].results. "
            "Do not put the project name inside results.\n"
            "B1 — What a label is: a name that stands on its own, the way it would appear on a slide "
            "or a badge ('Onboarding v2', 'Marketplace Checkout', 'ISO 27001 audit'). It is NOT the "
            "thing a verb acts on: in 'grew the development team from 3 to 11', the label is not 'the "
            "development team' — that bullet has no name, so leave label null and let the sentence be "
            "the context. A label that only makes sense with the verb in front of it is not a label.\n"
            "C — Education: type='education', company=institution name, role=degree/program, "
            "period=years. Short courses and certificates → type='credential', not a type of "
            "their own.\n"
            "D — Responsibilities vs highlights: explicit responsibility/duty bullets with no single "
            "crisp metric → responsibilities[]. Bullets with a concrete before/after metric → "
            "highlights[]. An achievement cluster with several unrelated points and no one metric also "
            "belongs in responsibilities[] — do not force it into a highlights[] entry with empty results[]. "
            "Western CVs typically have no responsibilities section — leave responsibilities: [].\n"
            "E — Target schema, not source format: always map content into the JSON shape above "
            "regardless of the source CV's own structure, heading levels, or language. Never mirror "
            "the source document's header depth or section order.\n"
            "F — Uncertainty: if something does not clearly belong in one bucket, do not guess — put "
            "it in hints[] instead.\n"
            "G — role_type_options: these are quick-pick suggestions for a wizard field the candidate "
            "confirms or overrides by hand, not a scored classification — err toward an empty array over "
            "a weak guess. Base them only on what the cases/highlights actually show (e.g. built "
            "something from scratch vs ran/optimized an existing operation — applies the same whether "
            "that's a codebase, a support queue, or a storefront), never on job title alone.\n"
            "H — Thin-CV domain hook (narrow exception to F): if pitch is null AND no cases[].domain has "
            "a value, still add ONE short domain/industry phrase to hints[] if the CV supports even a "
            "loose read (e.g. 'fintech background', 'early-stage startup experience') — this is a minor "
            "cover-letter-opener hook, not a scoring input, so a softer guess than F's general caution is "
            "acceptable here specifically, since the alternative (staying silent) leaves nothing for a "
            "cover letter to hook into at all. Do not add this hint when pitch or a case domain already "
            "covers it — only for the genuinely-thin case.\n"
            "- skills: professional skills only — NO metrics (AOV, CAC, TTR are metrics, not skills)\n"
            "- If a field is absent in the CV, use null or empty array/object\n"
            "- Do NOT invent or assume anything not explicitly present\n"
            "- target_market: default to 'cis' unless the CV's structure/content clearly indicates a "
            "western or global job search context\n"
            "- suggested_queries: 2-3 Russian-language HH.ru search queries matching this candidate's "
            "role; use terms job seekers actually type on hh.ru"
        )

    def _parse_json_response(self, raw: str, source_file: str) -> ResumeData:
        raw = raw.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            if _HAS_JSON_REPAIR:
                try:
                    parsed = json.loads(repair_json(raw))
                except Exception:
                    pass

        data = ResumeData(
            target_market=parsed.get("target_market") or "cis",
            locale=parsed.get("locale") or "",
            identity=parsed.get("identity") or {},
            pitch=parsed.get("pitch"),
            cases=parsed.get("cases") or [],
            skills=parsed.get("skills") or [],
            tools=parsed.get("tools") or [],
            languages=parsed.get("languages") or [],
            interests=parsed.get("interests") or [],
            hints=parsed.get("hints") or [],
            suggested_queries=parsed.get("suggested_queries") or [],
            career_profile_suggestions=parsed.get("career_profile_suggestions") or {},
            # wizard-filled only — never parsed from the CV
            career_profile={},
            logistics={},
            search={},
            rules={},
            source_file=source_file,
            parsed_at=datetime.now().isoformat(),
        )
        return self._finalize(data)

    # ── Hints ─────────────────────────────────────────────────────────────────

    def _finalize(self, data: ResumeData) -> ResumeData:
        # Preserve LLM-populated hints[] (Rule F, content-level) and append
        # structural hints — do not overwrite either.
        data.hints = list(data.hints or []) + self._build_hints(data)
        return data

    def _build_hints(self, data: ResumeData) -> list:
        identity = data.identity or {}
        hints = []
        if not identity.get("name"):     hints.append("Add your full name")
        if not identity.get("role"):     hints.append("Add your target job title")
        if not identity.get("location"): hints.append("Add your city/location")
        if len(data.skills) < 3:
            hints.append("Add at least 3 professional skills")
        if not data.cases:
            hints.append("Add work history — run wizard or edit candidate.md directly")
        if not identity.get("contacts"):
            hints.append("Add LinkedIn/GitHub/Telegram — helps LLM answer HR form contact questions")
        return hints
