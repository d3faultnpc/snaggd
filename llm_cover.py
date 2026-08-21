import json
import hashlib
import time
from typing import Tuple, List, Optional
from pathlib import Path
from config import CONFIG

from core.llm_agent import LLMAgent


def get_agent(data_dir: "Path") -> "LLMAgent | None":
    """Returns a fresh LLMAgent bound to the given profile directory.

    Used by adapter code that needs a one-off agent (e.g. modal dismissal).
    Not a singleton — each call creates a new instance. data_dir is required:
    see LLMAgent.__init__ for why there is no default.
    """
    try:
        return LLMAgent(data_dir=data_dir)
    except Exception as _e:
        print(f"   ⚠️ LLMAgent not available: {_e}")
        return None


class LLMCover:
    """Vacancy scorer + on-demand cover-letter generator, split score/cover caching.

    score() is called once per vacancy, early — before any gate (stop_match,
    min_score, dry_run) decides whether the vacancy proceeds at all. cover()
    is called on demand, only by whichever handler actually needs to send a
    real cover letter (ChatHandler's native "Добавить сопроводительное" flow,
    or hh_modal.py's own dedicated cover-letter step) — never eagerly. This
    replaces the old generate(), which always ran both unconditionally,
    paying for a full cover-generation call on every vacancy even when it was
    about to be filtered by score or a hard-block category (session 56).

    Score cache (llm_cache.json): keyed by compound hash of
      cover_model|llm_model|profile|vacancy_text → same description always hits cache.

    Cover cache (cover_cache.json): keyed by vacancy_id → each vacancy gets its
      own cover, so duplicate vacancies (same description, different ID) receive
      naturally varying text from the LLM instead of the same cached letter.
      Falls back to text_hash key when vacancy_id is not available. This is
      also what keeps cover() consistent if two different handlers end up
      needing one for the same vacancy (e.g. an hh_modal step without its own
      cover field, followed later by chatik) — the second call reuses the
      same cached text instead of generating a different one.

    Exposes last_score, last_matched_skills, last_gaps, last_stop_match,
    last_vacancy_role_type, last_signals after score(), and
    last_cover_template_name after cover() — so the adapter can read results
    without an extra LLM round-trip.
    """

    def __init__(self, data_dir: Path):
        if data_dir is None:
            raise ValueError("LLMCover requires an explicit data_dir (the active profile's directory)")
        self._data_dir = Path(data_dir)
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
        # Why the block was made: "text" (the posting says so) or
        # "company_knowledge" (it does not, but the employer is known to operate
        # there), plus the quote or fact behind it. A block on company knowledge
        # cannot be re-checked from the record later unless the record says so.
        self.last_stop_basis: Optional[str] = None
        self.last_stop_evidence: Optional[str] = None
        # What the model wanted to block on and we refused, with the reason.
        # A suppressed block is invisible from outside — the vacancy just carries
        # on — so without this the suppression could never be reviewed.
        self.last_stop_suppressed: Optional[dict] = None
        self.last_vacancy_role_type: Optional[str] = None
        # Whether the vacancy's contribution style matched the candidate's own
        # role_type. The scorer has always answered this; nothing carried it, so
        # a run could not show that the model reported "nothing to compare" on a
        # profile that plainly states a role_type.
        self.last_role_type_match: Optional[bool] = None
        # What the call itself did: provider generation id and token counts.
        # None on a cache hit, and that is the truth about a cache hit — there
        # was no call. Never written into self.cache for the same reason.
        self.last_call_meta: Optional[dict] = None
        self.last_signals: list = []
        self.last_cover_template_name: Optional[str] = None
        # The real exception text behind a `score() -> False` result — was
        # print()-only before (session 58 live incident: a run failed on
        # every vacancy, "model unavailable" in the GUI gave no way to tell
        # a real connection error from an auth/relay problem without
        # reading raw console output nobody has access to from the app).
        self.last_score_error: Optional[str] = None

    def score(self, vacancy_text: str) -> bool:
        """Scores the vacancy — the only LLM call needed before adapter.py's
        stop_match/min_score/dry_run gates decide whether a cover is even
        worth generating. Sets last_score/last_matched_skills/last_gaps/
        last_stop_match/last_vacancy_role_type/last_signals. Returns False
        only when the LLM is genuinely unavailable (all last_* reset to
        empty/zero) — caller treats that as skipped_llm_unavailable, same
        contract the old template_name == "static_fallback" check used to
        signal for the combined score+cover call.

        Cached by text hash — same description always reuses cached score,
        whether or not a cover ever ends up being generated for it.
        """
        text_for_processing = vacancy_text[:CONFIG.llm_max_input_chars]
        text_hash = self._hash_text(text_for_processing)
        self.last_score_error = None

        if text_hash in self.cache:
            print("   📋 Using cached score")
            self.last_signals = self._restore_score_from_cache(self.cache[text_hash])
            # Cleared, not left over: without this the record for a cached score
            # would carry the id and token counts of whatever vacancy was scored
            # before it — an observation attributed to the wrong call, which is
            # worse than no observation at all.
            self.last_call_meta = None
            self.last_role_type_match = None
            self.last_stop_suppressed = None
            return True

        if self._agent is None:
            self._reset_score_defaults()
            self.last_score_error = "LLMAgent not initialized (see startup log)"
            print("   📝 LLM unavailable — no score")
            return False

        try:
            score_data = self._agent.score_vacancy(text_for_processing)
        except Exception as e:
            print(f"   ⚠️ Score error: {e}")
            self._reset_score_defaults()
            self.last_score_error = f"{type(e).__name__}: {e}"
            return False

        self.last_score = score_data.get("score", 0)
        self.last_matched_skills = score_data.get("matched_skills", [])
        self.last_gaps = score_data.get("gaps", [])
        self.last_stop_match = score_data.get("stop_match", None)
        self.last_stop_basis = score_data.get("stop_basis", None)
        self.last_stop_evidence = score_data.get("stop_evidence", None)
        self.last_stop_suppressed = score_data.get("stop_suppressed", None)
        self.last_vacancy_role_type = score_data.get("vacancy_role_type", None)
        self.last_role_type_match = score_data.get("role_type_match", None)
        self.last_call_meta = score_data.get("call_meta", None)
        self.last_signals = score_data.get("signals", [])

        # Cover/template slots (indices 0/1) kept as placeholders — never read
        # back by _restore_score_from_cache, which only touches indices 2-7 —
        # so the array shape stays identical to the old generate()-written
        # entries and old cached entries keep working unchanged, no format
        # version bump needed.
        self.cache[text_hash] = [
            None, "pending", self.last_signals, self.last_score,
            self.last_matched_skills, self.last_gaps, self.last_stop_match,
            self.last_vacancy_role_type, self.last_stop_basis, self.last_stop_evidence,
        ]
        self._save_cache()
        print("   🤖 Scored via LLM")
        return True

    def cover(self, vacancy_text: str, vacancy_id: str = None) -> str:
        """Generates (or reuses) the cover letter — called on demand by
        whichever handler actually needs to send one. Must be called after
        score() in the same vacancy pass (uses its match context). Cached by
        vacancy_id: if a second handler/layer ends up needing a cover for the
        same vacancy, the second call reuses the exact same text instead of
        generating a different one — this is what actually closes #38's "two
        differing cover-shaped messages", not a send-side gate (session 56).
        Falls back to a minimal static cover if the LLM is unavailable.
        """
        text_for_processing = vacancy_text[:CONFIG.llm_max_input_chars]
        text_hash = self._hash_text(text_for_processing)
        # Profile hash in the key, same as the score cache gets via _hash_text().
        # It was missing here, and the asymmetry is not academic: a cover written
        # against one profile stayed pinned to its vacancy_id forever, so editing
        # the profile silently changed future scores but not future covers. The
        # 2026-08-11 profile wipe made that concrete — covers generated while the
        # profile was empty would have been reused verbatim after it was restored.
        cover_key = f"{vacancy_id or text_hash}:{self._profile_hash}"

        if cover_key in self.cover_cache:
            print("   📋 Using cached cover")
            cover_entry = self.cover_cache[cover_key]
            self.last_cover_template_name = cover_entry[1]
            return cover_entry[0]

        match_context = {
            "score": self.last_score,
            "matched_skills": self.last_matched_skills,
            "gaps": self.last_gaps,
            "stop_match": self.last_stop_match,
            "signals": self.last_signals,
            "vacancy_role_type": self.last_vacancy_role_type,
        }

        try:
            cover, template_name = self._generate_cover_only(text_for_processing, match_context)
            print("   🤖 Generated cover via LLM")
        except Exception as e:
            print(f"   ⚠️ Cover generation error: {e}")
            cover, template_name, _ = self._fallback_cover()

        self.last_cover_template_name = template_name
        if template_name != "static_fallback":
            self.cover_cache[cover_key] = [cover, template_name]
            self._save_cover_cache()

        return cover

    # ── Private helpers ───────────────────────────────────────────────────────

    def _reset_score_defaults(self) -> None:
        self.last_score = 0
        self.last_matched_skills = []
        self.last_gaps = []
        self.last_stop_match = None
        self.last_stop_basis = None
        self.last_stop_evidence = None
        self.last_stop_suppressed = None
        self.last_vacancy_role_type = None
        self.last_role_type_match = None
        self.last_call_meta = None
        self.last_signals = []

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
        """Short hash of candidate.md — changes when the user updates their profile.

        Hashes the full file, not a slice — an earlier version truncated to the
        first 500 chars before hashing, so an edit past that point left the hash
        (and therefore the cache) silently unchanged, serving stale scores/covers
        against the old profile content.
        """
        try:
            profile_path = self._data_dir / "candidate.md"
            content = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
            return hashlib.md5(content.encode('utf-8')).hexdigest()[:8]
        except Exception:
            return "noprofile"

    def _restore_score_from_cache(self, entry: list) -> list:
        """Restores last_score / skills / gaps / stop_match / vacancy_role_type from cache.

        Cache format v4: v3 + [stop_basis, stop_evidence] — appended, so a v3 entry
        still restores; it simply has no basis to restore, which is the truth about
        an entry written before a block had to state one.
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
            self.last_stop_basis = entry[8] if len(entry) >= 9 else None
            self.last_stop_evidence = entry[9] if len(entry) >= 10 else None
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
        """Calls generate_cover() using pre-computed match_context (score already set)."""
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

    def _fallback_cover(self) -> Tuple[str, str, List[str]]:
        """Static fallback when LLM is unavailable — returns a minimal cover letter."""
        return (
            "Hello.\n\nI am interested in this position and would be happy to discuss the details.",
            "static_fallback",
            []
        )
