import json
import hashlib
import time
from typing import Tuple, List, Optional
from pathlib import Path
from config import CONFIG

try:
    from core.llm_agent import LLMAgent
    _agent = LLMAgent()
except Exception as _e:
    _agent = None
    print(f"   ⚠️ LLMAgent not initialized: {_e} — using static fallback")

class LLMCover:
    """Cover letter generator with caching.

    Exposes last_score, last_matched_skills, last_gaps, last_stop_match
    after each generate() call so the adapter can read results without
    an extra LLM round-trip.

    Cache key: MD5 of vacancy text. Cache entry stores all fields so a
    cache hit restores every attribute (including stop_match) at zero cost.
    """

    def __init__(self):
        self.cache_file = Path(CONFIG.cache_file)
        self._profile_hash = self._compute_profile_hash()
        self.cache = self._load_cache()
        self.last_score: int = 0
        self.last_matched_skills: list = []
        self.last_gaps: list = []
        self.last_stop_match: Optional[str] = None

    def generate(self, vacancy_text: str) -> Tuple[str, str, List[str]]:
        """Score vacancy + generate cover letter in two LLM calls.

        Order: score first (may reveal stop_match), cover second.
        Returns: (cover_letter, template_name, signals)
        Side effects: sets last_score, last_matched_skills, last_gaps, last_stop_match.
        """
        text_for_processing = vacancy_text[:CONFIG.llm_max_input_chars]
        text_hash = self._hash_text(text_for_processing)

        if text_hash in self.cache:
            print("   📋 Using cached result")
            entry = self.cache[text_hash]
            # Cache format v2: [cover, template, signals, score, skills, gaps, stop_match]
            if len(entry) >= 7:
                self.last_score = entry[3]
                self.last_matched_skills = entry[4]
                self.last_gaps = entry[5]
                self.last_stop_match = entry[6]
            elif len(entry) >= 6:
                # v1 cache (no stop_match): restore what we have, stop_match unknown
                self.last_score = entry[3]
                self.last_matched_skills = entry[4]
                self.last_gaps = entry[5]
                self.last_stop_match = None
            return entry[:3]

        try:
            result = self._generate_with_llm(text_for_processing)
            print("   🤖 Generated via LLM")
        except Exception as e:
            print(f"   ⚠️ LLM error: {e}")
            result = self._fallback_cover()
            self.last_stop_match = None
            print("   📝 LLM unavailable — using static fallback")

        # Cache v2: cover + template + signals + score + skills + gaps + stop_match
        self.cache[text_hash] = list(result) + [
            self.last_score, self.last_matched_skills,
            self.last_gaps, self.last_stop_match,
        ]
        self._save_cache()

        return result
    
    def _hash_text(self, text: str) -> str:
        """Cache key: compound hash of (cover_model, llm_model, profile, vacancy_text).

        Any change to model, candidate profile, or vacancy text produces a new key.
        Stale entries from old models or profiles are ignored automatically.
        """
        cover_model = _agent.cover_model if _agent else ""
        llm_model = _agent.model if _agent else ""
        compound = f"{cover_model}|{llm_model}|{self._profile_hash}|{text}"
        return hashlib.md5(compound.encode('utf-8')).hexdigest()[:16]

    def _compute_profile_hash(self) -> str:
        """Short hash of candidate.md — changes when the user updates their profile."""
        try:
            profile_path = Path(CONFIG.data_dir) / "candidate.md"
            content = profile_path.read_text(encoding="utf-8")[:500] if profile_path.exists() else ""
            return hashlib.md5(content.encode('utf-8')).hexdigest()[:8]
        except Exception:
            return "noprofile"

    def _load_cache(self) -> dict:
        """Loads cache. Returns empty dict if file is from a previous day (session boundary)."""
        try:
            if self.cache_file.exists():
                age_hours = (time.time() - self.cache_file.stat().st_mtime) / 3600
                if age_hours > 24:
                    print("   📋 Cache expired (>24h) — starting fresh")
                    return {}
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                print(f"   📋 Cache loaded: {len(cache)} entries")
                return cache
        except Exception as e:
            print(f"   ⚠️ Cache load error: {e}")
        return {}
    
    def _save_cache(self) -> None:
        """Saves cache to file."""
        try:
            # Evict oldest entries when cache exceeds limit (simple FIFO)
            if len(self.cache) > CONFIG.cache_size:
                keys_to_remove = list(self.cache.keys())[:-CONFIG.cache_size]
                for key in keys_to_remove:
                    del self.cache[key]
            
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"   ⚠️ Cache save error: {e}")
    
    def _generate_with_llm(self, vacancy_text: str) -> Tuple[str, str, List[str]]:
        if _agent is None:
            raise RuntimeError("LLMAgent not available")
        # Score first: may reveal stop_match before spending tokens on cover letter.
        # If stop_match is set, the adapter will skip apply — cover is still cached
        # so the next encounter of the same vacancy costs 0 extra calls.
        score_data = _agent.score_vacancy(vacancy_text)
        self.last_score = score_data.get("score", 0)
        self.last_matched_skills = score_data.get("matched_skills", [])
        self.last_gaps = score_data.get("gaps", [])
        self.last_stop_match = score_data.get("stop_match", None)
        signals = score_data.get("signals", [])
        # Generate cover with scoring context: matched skills + signals + vacancy role type
        # so the model writes precisely to the real overlap, not from scratch.
        cover = _agent.generate_cover(vacancy_text, match_context=score_data)
        return cover, "llm", signals
    
    def _fallback_cover(self) -> Tuple[str, str, List[str]]:
        """Static fallback when LLM is unavailable — returns a minimal cover letter."""
        return (
            "Hello.\n\nI am interested in this position and would be happy to discuss the details.",
            "static_fallback",
            []
        )