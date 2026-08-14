"""
Adapter-agnostic stop filter loader.

Storage split:
  data/filters.json      — machine-only rules (exact matching, numeric thresholds).
                           Updated by wizard/settings UI. Never sent to LLM.
  data/job_preferences.md — LLM context (stop_categories for semantic detection live here).
                           Loaded into the LLM system prompt wholesale by llm_agent.py.

Three-tier filter model (applied by the adapter per vacancy):
  Level 0 — title_keywords    : exact substring in vacancy title. No LLM, no page open.
  Level 1 — companies         : exact substring in company name from DOM. No LLM, after page open.
  Level 1b — min_employer_rating : numeric rating from DOM. No LLM, after page open.
  Level 2 — categories        : LLM semantic detection via score_vacancy → stop_match field.
                                 Categories come from job_preferences.md (already in system prompt).

Adding support for a new adapter (Greenhouse, Lever):
  - Call load_stop_filters() the same way — config is site-agnostic.
  - Implement extract_company_name() and extract_employer_rating() in the adapter's browser module.
  - Level 2 is free: LLM reads stop_categories from system prompt regardless of adapter.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class StopFilters:
    title_keywords: list = field(default_factory=list)
    """Matched against vacancy title string. Hit → instant skip, no page open."""

    companies: list = field(default_factory=list)
    """Matched against company name from vacancy DOM. Hit → skip after page open."""

    categories: list = field(default_factory=list)
    """Semantic categories read from job_preferences.md → injected into LLM scoring prompt."""

    min_employer_rating: Optional[float] = None
    """Minimum employer review rating (e.g. 3.6 on a 1–5 scale). None = no filter."""

    min_match: Optional[int] = None
    """Per-profile override of CONFIG.min_score (0-100 LLM match threshold). None = use the
    global default — this is deliberately never LLM-extracted (resume-vs-vacancy comparison
    doesn't exist until scoring time), same storage/loading path as min_employer_rating."""

    def is_empty(self) -> bool:
        return not (
            self.title_keywords or self.companies
            or self.categories or self.min_employer_rating is not None
            or self.min_match is not None
        )

    def summary(self) -> str:
        parts = []
        if self.title_keywords:
            parts.append(f"title_kw={len(self.title_keywords)}")
        if self.companies:
            parts.append(f"companies={len(self.companies)}")
        if self.categories:
            parts.append(f"categories={len(self.categories)}")
        if self.min_employer_rating is not None:
            parts.append(f"min_rating≥{self.min_employer_rating}")
        if self.min_match is not None:
            parts.append(f"min_match≥{self.min_match}")
        return ", ".join(parts) if parts else "none"


_UNSET = object()
"""'Caller said nothing about this field.' Distinct from None, which means 'clear it'.

None used to mean both, and the two callers that pass it mean the second: the CLI wizard's
rating prompt says "Enter = no filter" and the GUI's says the same by leaving the box empty.
Under the old contract either one silently kept whatever rating was already on disk, so a
filter could be added and never removed. Nothing passed None meaning "skip" — omitting the
argument is how that has always been expressed."""


def patch_filters_json(data_dir: Path, *, stop_companies=_UNSET, stop_title_keywords=_UNSET,
                        min_employer_rating=_UNSET, min_match=_UNSET) -> None:
    """Merge caller-supplied hard-filter rules into data/filters.json. Shared writer for
    the wizard (onboarding) and the Settings API (live per-profile edits) — same file,
    same schema, single implementation instead of two copies drifting apart.

    Omit an argument to leave that rule alone; pass None to remove it."""
    path = data_dir / "filters.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        data = {}
    if not data.get("_comment"):
        data["_comment"] = "Machine-only stop rules. Edited by wizard/settings. Never sent to LLM."
    data.setdefault("stop_title_keywords", [])
    data.setdefault("stop_companies", [])
    if stop_companies is not _UNSET:
        data["stop_companies"] = stop_companies or []
    if stop_title_keywords is not _UNSET:
        data["stop_title_keywords"] = stop_title_keywords or []
    for key, value in (("min_employer_rating", min_employer_rating), ("min_match", min_match)):
        if value is _UNSET:
            continue
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_stop_filters(data_dir: Path) -> StopFilters:
    """
    Load stop filter config from two sources:
      1. data_dir/filters.json       → machine rules (title_kw, companies, min_rating)
      2. data_dir/job_preferences.md → stop_categories (LLM-semantic, parsed as fallback)

    Either file may be absent — returns what's available, empty StopFilters if both missing.
    """
    filters = StopFilters()
    _load_from_json(data_dir / "filters.json", filters)
    _load_categories_from_prefs(data_dir / "job_preferences.md", filters)
    return filters


# ── Private parsers ───────────────────────────────────────────────────────────

def _load_from_json(path: Path, filters: StopFilters) -> None:
    """Read filters.json and populate machine-only fields."""
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    filters.title_keywords = [kw.lower() for kw in data.get("stop_title_keywords", [])]
    filters.companies = [co.lower() for co in data.get("stop_companies", [])]

    rating = data.get("min_employer_rating")
    if rating is not None:
        try:
            filters.min_employer_rating = float(rating)
        except (TypeError, ValueError):
            pass

    match = data.get("min_match")
    if match is not None:
        try:
            filters.min_match = int(match)
        except (TypeError, ValueError):
            pass


def _load_categories_from_prefs(path: Path, filters: StopFilters) -> None:
    """
    Read stop_categories section from job_preferences.md.
    This file is primarily for LLM consumption; we parse categories so the
    adapter can also log them in blocked entries for dashboard display.
    """
    if not path.exists():
        return
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("stop_categories:"):
            current = "categories"
        elif line.startswith("  - ") and current == "categories":
            val = line[4:].strip().lower()
            if val and val not in filters.categories:
                filters.categories.append(val)
        elif line.strip() and not line.startswith(" ") and not line.startswith("#"):
            current = None
