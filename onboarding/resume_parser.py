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
import json
import os
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

_BLOCK_START = "<!-- snaggd:start -->"
_BLOCK_END = "<!-- snaggd:end -->"
_TOKEN_GUARD_CHARS = 6000


@dataclass
class ResumeData:
    schema_version: str = "1.0"
    target_market: str = ""
    locale: str = ""

    identity: dict = field(default_factory=dict)        # name, role, location, contacts: []
    pitch: Optional[str] = None

    # Wizard-filled only — never extracted from the CV itself (career_profile.role_type/edge
    # need the candidate's own framing; logistics/search/rules are filter/config data, not CV content)
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

    # Operational metadata — not schema content, used directly by Python code
    source_file: str = ""
    parsed_at: str = ""
    completeness: float = 0.0
    hints: list = field(default_factory=list)


def _typed_contact_line(raw: str) -> str:
    """Type-sniff a raw contact string into a labeled line for MD rendering."""
    low = raw.lower()
    if "t.me/" in low or raw.startswith("@"):
        return f"telegram: {raw}"
    if "github.com" in low:
        return f"github: {raw}"
    if "linkedin.com" in low:
        return f"linkedin: {raw}"
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

    # Gemini Flash reads PDF and images natively via image_url — same as Health-concierge
    MULTIMODAL_MODEL = os.getenv("RESUME_PARSE_MODEL", "google/gemini-2.0-flash-001")
    TEXT_MODEL       = os.getenv("LLM_MODEL", "anthropic/claude-3-5-haiku")

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

        existing_content: previous file content, if any. Content after the managed block
        end-marker is preserved verbatim (user's own free-text annotations survive re-runs).
        """
        body = self._render_managed_block(data)
        if len(body) > _TOKEN_GUARD_CHARS:
            print(f"⚠️  candidate.md managed block exceeds {_TOKEN_GUARD_CHARS} chars — shortening", file=sys.stderr)
            body = self._render_managed_block(self._shorten_for_token_guard(data))

        managed = f"{_BLOCK_START}\n{body}\n{_BLOCK_END}"

        user_section = ""
        if existing_content and _BLOCK_END in existing_content:
            user_section = existing_content.split(_BLOCK_END, 1)[1]

        return managed + user_section

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render_managed_block(self, data: ResumeData) -> str:
        identity = data.identity or {}
        role = identity.get("role") or "MISSING — add your target role"
        lines = [f"# {role}"]
        updated = data.parsed_at[:10] if data.parsed_at else ""
        lines.append(f"# completeness: {data.completeness:.0%} | source: {data.source_file} | updated: {updated}")

        # ── Identity ───────────────────────────────────────────────────────
        lines += ["", "## Identity"]
        lines.append(f"name: {identity.get('name') or 'MISSING — add your name'}")
        lines.append(f"location: {identity.get('location') or '# HINT: add your city — used for relocation/location HR questions'}")
        contacts = identity.get("contacts") or []
        if contacts:
            for c in contacts:
                lines.append(_typed_contact_line(c))
        else:
            lines.append("# HINT: add LinkedIn/GitHub/Telegram/email — used to answer HR form contact questions")

        if data.pitch:
            lines += ["", data.pitch]

        # ── Career Profile ────────────────────────────────────────────────
        lines += ["", "## Career Profile"]
        cp = data.career_profile or {}
        lines.append(f"role_type: {cp['role_type']}" if cp.get("role_type")
                     else "role_type: # SKIPPABLE — fill via wizard if useful for scoring (free text, no fixed list)")
        lines.append(f"edge: {cp['edge']}" if cp.get("edge")
                     else "edge: # HINT: one-sentence unique angle vs other candidates")
        lines.append(f"aspiration: {cp['aspiration']}" if cp.get("aspiration")
                     else "aspiration: # HINT: direction you want to move toward, even if your cases don't show it yet")

        # ── Relocation & Work Format ─────────────────────────────────────
        lines += ["", "## Relocation & Work Format"]
        lg = data.logistics or {}
        if lg.get("relocation") or lg.get("work_format"):
            if lg.get("relocation"):
                lines.append(f"relocation: {lg['relocation']}")
            if lg.get("work_format"):
                lines.append(f"work_format: {lg['work_format']}")
        else:
            lines.append("# — empty · SKIPPABLE, agent uses defaults, never fabricates")

        # ── Desired Salary ────────────────────────────────────────────────
        lines += ["", "## Desired Salary"]
        salary = (data.search or {}).get("salary")
        lines.append(salary if salary else "# — empty · SKIPPABLE, agent uses market average, never fabricates")

        # ── Skills ────────────────────────────────────────────────────────
        lines += ["", "## Skills"]
        if data.skills:
            lines += [f"- {s}" for s in data.skills]
        else:
            lines.append("# EMPTY — add professional skills (e.g. platform thinking, API design, SQL)")

        # ── Work Experience / Education / Projects & Credentials ─────────
        _EDU_TYPES = {"education"}
        _PROJECT_TYPES = {"project", "certification", "publication", "volunteering", "research"}
        edu_cases = [c for c in data.cases if c.get("type") in _EDU_TYPES]
        project_cases = [c for c in data.cases if c.get("type") in _PROJECT_TYPES]
        # Catch-all: anything not education/project-family renders as Work Experience —
        # including None and any type value outside the known set, so an unrecognized
        # `type` from LLM output never silently disappears from candidate.md.
        work_cases = [c for c in data.cases
                      if c.get("type") not in _EDU_TYPES and c.get("type") not in _PROJECT_TYPES]

        lines += ["", "## Work Experience"]
        if work_cases:
            for case in work_cases:
                lines += self._render_case(case, data.target_market, include_zone=True)
        else:
            lines.append("# EMPTY — add work history via wizard or edit candidate.md directly")

        if edu_cases:
            lines += ["", "## Education"]
            for case in edu_cases:
                lines += self._render_case(case, data.target_market, include_zone=False)

        lines += ["", "## Projects & Credentials"]
        if project_cases:
            for case in project_cases:
                lines += self._render_case(case, data.target_market, include_zone=False)
        else:
            lines.append("# SKIPPABLE — add personal/side projects, certifications, publications")

        # ── Tools ─────────────────────────────────────────────────────────
        lines += ["", "## Tools"]
        lines.append(", ".join(data.tools) if data.tools else "# HINT: add tools you use (Jira, Figma, SQL, etc.)")

        # ── Languages ─────────────────────────────────────────────────────
        lines += ["", "## Languages"]
        if data.languages:
            for lang in data.languages:
                note = f" ({lang['note']})" if lang.get("note") else ""
                lines.append(f"{lang.get('lang', '')}: {lang.get('level', '')}{note}")
        else:
            lines.append("# HINT: add language proficiency (e.g. english: B2, russian: native)")

        # ── Additional ────────────────────────────────────────────────────
        lines += ["", "## Additional"]
        added_any = False
        if data.interests:
            lines.append(f"interests: {', '.join(data.interests)}")
            added_any = True
        if data.hints:
            lines.append("hints (low-confidence — verify before relying on):")
            lines += [f"- {h}" for h in data.hints]
            added_any = True
        if not added_any:
            lines.append("# — empty")

        return "\n".join(lines)

    def _render_case(self, case: dict, target_market: str, include_zone: bool) -> list:
        header_parts = [x for x in [case.get("company"), case.get("role"),
                                     case.get("period"), case.get("domain")] if x]
        lines = ["", f"### {' | '.join(header_parts) if header_parts else 'MISSING — company/role/period'}"]

        highlights = case.get("highlights") or []
        responsibilities = case.get("responsibilities") or []

        ctx = case.get("context")
        if ctx and not highlights and not responsibilities:
            # Bare context-only case (e.g. an earlier role at the same company, no metrics)
            lines.append(f"Context: {ctx}")

        if include_zone and target_market != "western" and target_market != "global" and responsibilities:
            lines += ["", "#### Zone of Responsibility"]
            lines += [f"- {r}" for r in responsibilities]

        for h in highlights:
            label, h_ctx, results = h.get("label"), h.get("context"), (h.get("results") or [])
            if label:
                lines += ["", f"#### {label}"]
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
            max_tokens=2500,
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
            max_tokens=2500,
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
            '    "role": "Current/target job title, combined with specialization if the CV states one '
            '(e.g. \'Product Manager, fintech\') or null",\n'
            '    "location": "City or null",\n'
            '    "contacts": ["raw contact strings — URLs, @handles, emails, phone numbers, exactly as found"]\n'
            '  },\n'
            '  "pitch": "1-2 sentence narrative summary/elevator pitch, only if the CV has one, else null",\n'
            '  "cases": [\n'
            '    {\n'
            '      "type": "employment | education | project | certification | publication | volunteering | research",\n'
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
            "C — Education: type='education', company=institution name, role=degree/program, "
            "period=years. Short courses/certifications → type='certification'.\n"
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
            # wizard-filled only — never parsed from the CV
            career_profile={},
            logistics={},
            search={},
            rules={},
            source_file=source_file,
            parsed_at=datetime.now().isoformat(),
        )
        return self._finalize(data)

    # ── Completeness & hints ──────────────────────────────────────────────────

    def _finalize(self, data: ResumeData) -> ResumeData:
        data.completeness = self._completeness_score(data)
        # Preserve LLM-populated hints[] (Rule F, content-level) and append
        # completeness-driven structural hints — do not overwrite either.
        data.hints = list(data.hints or []) + self._build_hints(data)
        return data

    def _completeness_score(self, data: ResumeData) -> float:
        identity = data.identity or {}
        tier1 = [identity.get("name"), identity.get("role"), identity.get("location")]
        t1 = sum(1 for f in tier1 if f) / len(tier1) * 0.35

        has_cases = bool(data.cases)
        has_evidence = any((c.get("highlights") or c.get("responsibilities")) for c in data.cases)
        tier2 = [len(data.skills) >= 3, has_cases, has_evidence]
        t2 = sum(tier2) / len(tier2) * 0.40

        has_contact = bool(identity.get("contacts"))
        tier3 = [len(data.tools) >= 1, bool(data.languages), has_contact]
        t3 = sum(tier3) / len(tier3) * 0.25

        return round(t1 + t2 + t3, 2)

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
