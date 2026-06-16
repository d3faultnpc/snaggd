import json
import hashlib
import time
from typing import Tuple, List, Optional
from pathlib import Path
from config import CONFIG

from core.llm_agent import LLMAgent


def get_agent(data_dir: "Path | None" = None) -> "LLMAgent | None":
    """Returns a fresh LLMAgent for the given data_dir (or CONFIG default).

    Used by adapter code that needs a one-off agent (e.g. modal dismissal).
    Not a singleton — each call creates a new instance.
    """
    try:
        return LLMAgent(data_dir=data_dir)
    except Exception as _e:
        print(f"   ⚠️ LLMAgent not available: {_e}")
        return None


class LLMCover:
    """Cover letter generator with split score/cover caching.

    Score cache (llm_cache.json): keyed by compound hash of
      cover_model|llm_model|profile|vacancy_text → same description always hits cache.

    Cover cache (cover_cache.json): keyed by vacancy_id → each vacancy gets its
      own cover, so duplicate vacancies (same description, different ID) receive
      naturally varying text from the LLM instead of the same cached letter.
      Falls back to text_hash key when vacancy_id is not available.

    Exposes last_score, last_matched_skills, last_gaps, last_stop_match
    after each generate() call so the adapter can read results without
    an extra LLM round-trip.
    """

    def __init__(self, data_dir: Path = None):
        self._data_dir = data_dir or CONFIG.data_dir
        self.cache_file = self._data_dir / "llm_cache.json"
        self.cover_cache_file = self._data_dir / "cover_cache.json"
        try:
            self._agent = LLMAgent(data_dir=self._data_dir)
        except Exception as _e:
            self._agent = None
            print(f"   ⚠️ LLMAgent not initialized: {_e} — using static fallback")
        self._profile_hash = self._compute_profile_hash()
        self.cache = self._load_cache()
        self.cover_cache = self._load_cover_cache()
        self.last_score: int = 0
        self.last_matched_skills: list = []
        self.last_gaps: list = []
        self.last_stop_match: Optional[str] = None
        self.last_vacancy_role_type: Optional[str] = None

    def generate(self, vacancy_text: str, vacancy_id: str = None) -> Tuple[str, str, List[str]]:
        """Score vacancy + generate cover letter.

        Score is cached by text hash — same description always reuses cached score.
        Cover is cached by vacancy_id — each vacancy gets its own letter, so
        duplicate vacancies (same description, different ID) get fresh LLM output.

        Falls back to text_hash as cover key when vacancy_id is unavailable.
        Returns: (cover_letter, template_name, signals)
        Side effects: sets last_score, last_matched_skills, last_gaps, last_stop_match.
        """
        text_for_processing = vacancy_text[:CONFIG.llm_max_input_chars]
        text_hash = self._hash_text(text_for_processing)
        cover_key = vacancy_id if vacancy_id else text_hash

        # ── Score lookup ──────────────────────────────────────────────────────
        if text_hash in self.cache:
            print("   📋 Using cached score")
            signals = self._restore_score_from_cache(self.cache[text_hash])
            match_context = {
                "score": self.last_score,
                "matched_skills": self.last_matched_skills,
                "gaps": self.last_gaps,
                "stop_match": self.last_stop_match,
                "signals": signals,
                "vacancy_role_type": self.last_vacancy_role_type,
            }

            # ── Cover lookup ──────────────────────────────────────────────────
            if cover_key in self.cover_cache:
                print("   📋 Using cached cover")
                cover_entry = self.cover_cache[cover_key]
                return cover_entry[0], cover_entry[1], signals

            # Score hit, cover miss → generate fresh cover (duplicate vacancy path)
            print("   🤖 Score cached — generating fresh cover...")
            try:
                cover, template_name = self._generate_cover_only(text_for_processing, match_context)
            except Exception as e:
                print(f"   ⚠️ Cover generation error: {e}")
                cover, template_name, _ = self._fallback_cover()

            if template_name != "static_fallback":
                self.cover_cache[cover_key] = [cover, template_name]
                self._save_cover_cache()

            return cover, template_name, signals

        # ── Full LLM call (score + cover) ─────────────────────────────────────
        try:
            result = self._generate_with_llm(text_for_processing)
            print("   🤖 Generated via LLM")
        except Exception as e:
            print(f"   ⚠️ LLM error: {e}")
            result = self._fallback_cover()
            self.last_score = 0
            self.last_matched_skills = []
            self.last_gaps = []
            self.last_stop_match = None
            print("   📝 LLM unavailable — using static fallback")

        cover, template_name_result, signals = result

        # Don't cache static_fallback — transient LLM error should not block
        # future sessions from getting a real cover for the same vacancy.
        if template_name_result != "static_fallback":
            self.cache[text_hash] = [cover, template_name_result, signals,
                                      self.last_score, self.last_matched_skills,
                                      self.last_gaps, self.last_stop_match,
                                      self.last_vacancy_role_type]
            self._save_cache()
            self.cover_cache[cover_key] = [cover, template_name_result]
            self._save_cover_cache()

        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _hash_text(self, text: str) -> str:
        """Cache key: compound hash of (cover_model, llm_model, profile, vacancy_text).

        Any change to model, candidate profile, or vacancy text produces a new key.
        Stale entries from old models or profiles are ignored automatically.
        """
        cover_model = self._agent.cover_model if self._agent else ""
        llm_model = self._agent.model if self._agent else ""
        compound = f"{cover_model}|{llm_model}|{self._profile_hash}|{text}"
        return hashlib.md5(compound.encode('utf-8')).hexdigest()[:16]

    def _compute_profile_hash(self) -> str:
        """Short hash of candidate.md — changes when the user updates their profile."""
        try:
            profile_path = self._data_dir / "candidate.md"
            content = profile_path.read_text(encoding="utf-8")[:500] if profile_path.exists() else ""
            return hashlib.md5(content.encode('utf-8')).hexdigest()[:8]
        except Exception:
            return "noprofile"

    def _restore_score_from_cache(self, entry: list) -> list:
        """Restores last_score / skills / gaps / stop_match / vacancy_role_type from cache.

        Cache format v3: [cover, template, signals, score, skills, gaps, stop_match, role_type]
        Cache format v2: [cover, template, signals, score, skills, gaps, stop_match]
        Cache format v1: [cover, template, signals, score, skills, gaps]
        Returns: signals list.
        """
        if len(entry) >= 7:
            self.last_score = entry[3]
            self.last_matched_skills = entry[4]
            self.last_gaps = entry[5]
            self.last_stop_match = entry[6]
            self.last_vacancy_role_type = entry[7] if len(entry) >= 8 else None
        elif len(entry) >= 6:
            self.last_score = entry[3]
            self.last_matched_skills = entry[4]
            self.last_gaps = entry[5]
            self.last_stop_match = None
        else:
            self.last_score = 0
            self.last_matched_skills = []
            self.last_gaps = []
            self.last_stop_match = None
        return entry[2] if len(entry) >= 3 else []

    def _generate_cover_only(self, vacancy_text: str, match_context: dict) -> Tuple[str, str]:
        """Calls generate_cover() using pre-computed match_context (score cached)."""
        if self._agent is None:
            raise RuntimeError("LLMAgent not available")
        cover = self._humanize(self._agent.generate_cover(vacancy_text, match_context=match_context))
        return cover, "llm"

    def _humanize(self, text: str) -> str:
        """Post-process LLM output: replace typographic characters not on a standard keyboard.

        Prompt-level rules alone cannot override model training priors for these
        high-frequency tokens. Deterministic replacement here guarantees output
        regardless of model behaviour.
        """
        return (text
                .replace('ё', 'е').replace('Ё', 'Е')
                .replace('—', '-')   # em-dash
                .replace('–', '-'))  # en-dash

    def _load_cache(self) -> dict:
        """Loads score cache. Returns empty dict if file is from a previous day."""
        try:
            if self.cache_file.exists():
                age_hours = (time.time() - self.cache_file.stat().st_mtime) / 3600
                if age_hours > 24:
                    print("   📋 Score cache expired (>24h) — starting fresh")
                    return {}
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                print(f"   📋 Score cache loaded: {len(cache)} entries")
                return cache
        except Exception as e:
            print(f"   ⚠️ Score cache load error: {e}")
        return {}

    def _save_cache(self) -> None:
        """Saves score cache to file."""
        try:
            if len(self.cache) > CONFIG.cache_size:
                keys_to_remove = list(self.cache.keys())[:-CONFIG.cache_size]
                for key in keys_to_remove:
                    del self.cache[key]
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"   ⚠️ Score cache save error: {e}")

    def _load_cover_cache(self) -> dict:
        """Loads cover cache keyed by vacancy_id. Same 24h TTL as score cache."""
        try:
            if self.cover_cache_file.exists():
                age_hours = (time.time() - self.cover_cache_file.stat().st_mtime) / 3600
                if age_hours > 24:
                    return {}
                with open(self.cover_cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                print(f"   📋 Cover cache loaded: {len(cache)} entries")
                return cache
        except Exception as e:
            print(f"   ⚠️ Cover cache load error: {e}")
        return {}

    def _save_cover_cache(self) -> None:
        """Saves cover cache to file."""
        try:
            if len(self.cover_cache) > CONFIG.cache_size:
                keys_to_remove = list(self.cover_cache.keys())[:-CONFIG.cache_size]
                for key in keys_to_remove:
                    del self.cover_cache[key]
            with open(self.cover_cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cover_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"   ⚠️ Cover cache save error: {e}")

    def _generate_with_llm(self, vacancy_text: str) -> Tuple[str, str, List[str]]:
        if self._agent is None:
            raise RuntimeError("LLMAgent not available")
        # Score first: may reveal stop_match before spending tokens on cover letter.
        # If stop_match is set, the adapter will skip apply — cover is still cached
        # so the next encounter of the same vacancy costs 0 extra calls.
        score_data = self._agent.score_vacancy(vacancy_text)
        self.last_score = score_data.get("score", 0)
        self.last_matched_skills = score_data.get("matched_skills", [])
        self.last_gaps = score_data.get("gaps", [])
        self.last_stop_match = score_data.get("stop_match", None)
        self.last_vacancy_role_type = score_data.get("vacancy_role_type", None)
        signals = score_data.get("signals", [])
        # Generate cover with scoring context: matched skills + signals + vacancy role type
        # so the model writes precisely to the real overlap, not from scratch.
        cover = self._humanize(self._agent.generate_cover(vacancy_text, match_context=score_data))
        return cover, "llm", signals

    def _fallback_cover(self) -> Tuple[str, str, List[str]]:
        """Static fallback when LLM is unavailable — returns a minimal cover letter."""
        return (
            "Hello.\n\nI am interested in this position and would be happy to discuss the details.",
            "static_fallback",
            []
        )
