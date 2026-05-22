import json
import hashlib
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
    """Cover letter generator with caching."""

    def __init__(self):
        self.cache_file = Path(CONFIG.cache_file)
        self.cache = self._load_cache()
        self.last_score: int = 0
        self.last_matched_skills: list = []
        self.last_gaps: list = []
        
    def generate(self, vacancy_text: str) -> Tuple[str, str, List[str]]:
        """
        Generates a cover letter.
        Returns: (cover_letter, template_name, signals)
        """
        text_for_processing = vacancy_text[:CONFIG.llm_max_input_chars]
        text_hash = self._hash_text(text_for_processing)

        if text_hash in self.cache:
            print("   📋 Using cached cover letter")
            entry = self.cache[text_hash]
            # Restore score fields if cache entry includes them (new format: 6 elements)
            if len(entry) >= 6:
                self.last_score = entry[3]
                self.last_matched_skills = entry[4]
                self.last_gaps = entry[5]
            return entry[:3]

        try:
            result = self._generate_with_llm(text_for_processing)
            print("   🤖 Generated via LLM")
        except Exception as e:
            print(f"   ⚠️ LLM error: {e}")
            result = self._fallback_cover()
            print("   📝 LLM unavailable — using static fallback")

        # Store cover + score data together so cache hits restore score correctly
        self.cache[text_hash] = list(result) + [self.last_score, self.last_matched_skills, self.last_gaps]
        self._save_cache()

        return result
    
    def _hash_text(self, text: str) -> str:
        """Creates an MD5 hash for vacancy text (used as cache key)."""
        return hashlib.md5(text.encode('utf-8')).hexdigest()[:16]
    
    def _load_cache(self) -> dict:
        """Loads cache from file."""
        try:
            if self.cache_file.exists():
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
        cover = _agent.generate_cover(vacancy_text)
        score_data = _agent.score_vacancy(vacancy_text)
        self.last_score = score_data.get("score", 0)
        self.last_matched_skills = score_data.get("matched_skills", [])
        self.last_gaps = score_data.get("gaps", [])
        signals = score_data.get("signals", [])
        return cover, "llm", signals
    
    def _fallback_cover(self) -> Tuple[str, str, List[str]]:
        """Static fallback when LLM is unavailable — returns a minimal cover letter."""
        return (
            "Hello.\n\nI am interested in this position and would be happy to discuss the details.",
            "static_fallback",
            []
        )