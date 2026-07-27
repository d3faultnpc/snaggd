"""
Unit tests for LLMCover.score()/cover() — the split score/cover generation
introduced session 56. Was one combined generate() call that ran cover
unconditionally, even for vacancies about to be filtered by score/stop_match
(confirmed live cost issue), and threaded one pre-baked cover_letter string
through the whole handler pipeline (root cause of #38's differing-message
duplicate). No real LLM calls — LLMAgent is mocked.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        from llm_cover import LLMCover


def _make_cover(tmp_dir, score_return=None, cover_return=None,
                 score_side_effect=None, cover_side_effect=None):
    """Builds an LLMCover with a mocked _agent, backed by a fresh temp data_dir."""
    cover = LLMCover.__new__(LLMCover)
    cover._data_dir = Path(tmp_dir)
    cover.cache_file = cover._data_dir / "llm_cache.json"
    cover.cover_cache_file = cover._data_dir / "cover_cache.json"
    cover._profile_hash = "testhash"
    cover.cache = {}
    cover.cover_cache = {}
    cover.last_score = 0
    cover.last_matched_skills = []
    cover.last_gaps = []
    cover.last_stop_match = None
    cover.last_vacancy_role_type = None
    cover.last_signals = []
    cover.last_cover_template_name = None

    agent = MagicMock()
    agent.cover_model = "test-model"
    agent.model = "test-model"
    if score_side_effect:
        agent.score_vacancy.side_effect = score_side_effect
    else:
        agent.score_vacancy.return_value = score_return or {
            "score": 70, "matched_skills": ["a"], "gaps": ["b"],
            "stop_match": None, "vacancy_role_type": "pm", "signals": ["sig1"],
        }
    if cover_side_effect:
        agent.generate_cover.side_effect = cover_side_effect
    else:
        agent.generate_cover.return_value = cover_return or "Generated cover text"
    cover._agent = agent
    return cover, agent


results = []


def check(label, condition):
    status = "✅" if condition else "❌"
    print(f"  {status} {label}")
    results.append(bool(condition))


# ── score() calls the LLM once, populates last_* including new last_signals ──
with tempfile.TemporaryDirectory() as tmp:
    cover, agent = _make_cover(tmp)
    ok = cover.score("some vacancy text")
    check("score() returns True on success", ok is True)
    check("score() calls score_vacancy exactly once", agent.score_vacancy.call_count == 1)
    check("score() does NOT call generate_cover", agent.generate_cover.call_count == 0)
    check("last_score set", cover.last_score == 70)
    check("last_signals set (new attribute)", cover.last_signals == ["sig1"])
    check("last_gaps set", cover.last_gaps == ["b"])

# ── score() cache hit skips the LLM entirely ──
with tempfile.TemporaryDirectory() as tmp:
    cover, agent = _make_cover(tmp)
    cover.score("some vacancy text")
    agent.score_vacancy.reset_mock()
    ok2 = cover.score("some vacancy text")
    check("score() cache hit still returns True", ok2 is True)
    check("score() cache hit does not call LLM again", agent.score_vacancy.call_count == 0)

# ── cover() is never called just from scoring — the actual cost fix ──
with tempfile.TemporaryDirectory() as tmp:
    cover, agent = _make_cover(tmp)
    cover.score("vacancy that would be filtered by score/stop_match")
    check("generate_cover never fires from score() alone (the cost bug this fixes)",
          agent.generate_cover.call_count == 0)

# ── cover() generates once, caches by vacancy_id ──
with tempfile.TemporaryDirectory() as tmp:
    cover, agent = _make_cover(tmp, cover_return="Real cover text")
    cover.score("vacancy text")
    text1 = cover.cover("vacancy text", vacancy_id="12345")
    check("cover() returns generated text", text1 == "Real cover text")
    check("cover() calls generate_cover exactly once", agent.generate_cover.call_count == 1)
    check("last_cover_template_name set to llm", cover.last_cover_template_name == "llm")

    # ── THE #38 FIX — a second cover() call for the SAME vacancy_id reuses
    # the cached text instead of generating a different one (simulates an
    # hh_modal step and a later chat layer both needing "the" cover) ──
    text2 = cover.cover("vacancy text", vacancy_id="12345")
    check("second cover() call for same vacancy_id returns IDENTICAL text", text2 == text1)
    check("second cover() call does NOT call the LLM again", agent.generate_cover.call_count == 1)

# ── different vacancy_id (duplicate posting) gets its own generation ──
with tempfile.TemporaryDirectory() as tmp:
    cover, agent = _make_cover(tmp)
    cover.score("vacancy text")
    agent.generate_cover.return_value = "cover A"
    cover.cover("vacancy text", vacancy_id="AAA")
    agent.generate_cover.return_value = "cover B"
    text_b = cover.cover("vacancy text", vacancy_id="BBB")
    check("different vacancy_id gets a fresh cover, not the first one's cache", text_b == "cover B")
    check("generate_cover called twice for two different vacancy_ids", agent.generate_cover.call_count == 2)

# ── score() LLM-unavailable path resets defaults and returns False ──
with tempfile.TemporaryDirectory() as tmp:
    cover, agent = _make_cover(tmp, score_side_effect=RuntimeError("boom"))
    cover.last_score = 999  # poison to prove reset happens
    ok = cover.score("vacancy text")
    check("score() returns False on LLM error", ok is False)
    check("score() resets last_score to 0 on error", cover.last_score == 0)

# ── cover() LLM-unavailable falls back to static cover, doesn't cache it ──
with tempfile.TemporaryDirectory() as tmp:
    cover, agent = _make_cover(tmp, cover_side_effect=RuntimeError("boom"))
    cover.score("vacancy text")
    text = cover.cover("vacancy text", vacancy_id="X1")
    check("cover() falls back to static text on error", "interested in this position" in text)
    check("last_cover_template_name is static_fallback", cover.last_cover_template_name == "static_fallback")
    check("static fallback is NOT cached", "X1" not in cover.cover_cache)

# ── old-format score cache entries (pre-session-56, 8-element array with a
# real cover/template at [0]/[1]) still load correctly — no format break ──
with tempfile.TemporaryDirectory() as tmp:
    from config import CONFIG as _CONFIG
    cover, agent = _make_cover(tmp)
    text_hash = cover._hash_text("legacy vacancy text"[:_CONFIG.llm_max_input_chars])
    cover.cache[text_hash] = ["old cover text", "llm", ["legacy_signal"], 55,
                              ["skillX"], ["gapY"], None, "role_old"]
    ok = cover.score("legacy vacancy text")
    check("score() reads a pre-existing v3-format cache entry without error", ok is True)
    check("legacy entry's score restored correctly", cover.last_score == 55)
    check("legacy entry's signals restored correctly", cover.last_signals == ["legacy_signal"])
    check("score_vacancy not called — legacy cache entry satisfied the lookup",
          agent.score_vacancy.call_count == 0)

print()
total = len(results)
passed = sum(results)
print(f"{passed}/{total} passed")
if passed != total:
    sys.exit(1)
