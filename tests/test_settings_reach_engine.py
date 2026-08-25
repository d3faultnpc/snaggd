"""
Does a setting the user configured actually reach the code that acts on it?

Why this file exists (2026-08-14): an audit of every user-facing control found
nine that did not. The pattern was always the same — two paths to one setting,
the UI writing down one and the engine reading the other. The engine reads a
profile's configuration from exactly three files:

    filters.json        machine rules, never sent to the model
    candidate.md        everything the model reads
    search_urls.txt     where to look

and candidate.json is NOT one of them; it holds the wizard's saved answers so a
re-run can prefill. The wizard had been writing its stop rules, its employer
rating and its work format into candidate.json alone, so "company or word —
instant skip" skipped nothing, and a rating filter filtered nothing.

The precedent for this kind of test is tests/test_data_dir_is_explicit.py: the
class of bug had already been fixed once by hand and came back, so the fix
became a test. Same here — the point is not that today's code is right, it is
that the next setting added has somewhere to fail loudly.

Pure filesystem + in-process HTTP. No network, no LLM, no browser.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def run():
    os.environ.setdefault("LLM_API_KEY", "test")
    os.environ["API_KEY"] = "test-key-for-settings-reach"

    import api
    from config import CONFIG
    from fastapi.testclient import TestClient
    from utils.filters import load_stop_filters, patch_filters_json

    root = Path(tempfile.mkdtemp(prefix="snaggd-reach-"))
    real_profiles_dir = api.PROFILES_DIR
    api.PROFILES_DIR = root / "profiles"
    assert api.PROFILES_DIR != real_profiles_dir, "refusing to run against the live profile tree"

    client = TestClient(api.app)
    headers = {"X-API-Key": CONFIG.api_key}
    failures = []

    def check(label, condition):
        print(f"  {'✅' if condition else '❌'} {label}")
        if not condition:
            failures.append(label)

    def save(profile, **candidate):
        base = {"identity": {"name": "T", "role": "PM"}, "skills": ["SQL"],
                "cases": [], "tools": [], "languages": []}
        base.update(candidate)
        return client.post("/api/v1/onboarding/save", headers=headers,
                           json={"profile": profile, "candidate": base})

    try:
        # ── stop rules land where the apply loop reads them ────────────────
        r = save("reach", rules={"stop": ["Acme", "junior"], "penalize": ["outsource", "pmm"],
                                 "min_employer_rating": 3.6})
        check(f"the save succeeds (HTTP {r.status_code})", r.status_code == 200)

        prof = api.PROFILES_DIR / "reach"
        filters = load_stop_filters(prof)
        check("`Never apply to` reaches the title tier — the one that skips before opening a page",
              "acme" in filters.title_keywords and "junior" in filters.title_keywords)
        check("`Never apply to` reaches the company tier too",
              "acme" in filters.companies and "junior" in filters.companies)
        check("`Min employer rating` reaches filters.json", filters.min_employer_rating == 3.6)

        # ── preferences land in the file the model reads ───────────────────
        md = (prof / "candidate.md").read_text(encoding="utf-8")
        check("`Rather not` reaches candidate.md as not_looking_for — the line that already works",
              "not_looking_for: outsource, pmm" in md)

        r = save("reach", logistics={"work_format": "Hybrid"})
        md = (prof / "candidate.md").read_text(encoding="utf-8")
        check("`Work format` reaches candidate.md instead of being dropped on save",
              "work_format: Hybrid" in md)

        # candidate.md answers "how well does this vacancy fit THIS person". A list of roles
        # being hunted describes the wanted vacancy instead, and in the same file it gives
        # the model a second thing to compare against — it can end up scoring the sought
        # role against the resume rather than the actual vacancy against the resume. The
        # feed is expressed by the HH wise link, not here.
        save("reach", search={"queries": ["Product Manager", "Head of Product"]})
        md = (prof / "candidate.md").read_text(encoding="utf-8")
        check("a target-role list never reaches candidate.md — the scorer's comparison "
              "has exactly one candidate in it",
              "Head of Product" not in md)

        # That save mentioned no rules at all. A payload that says nothing about a
        # setting must not decide anything about it — the first draft of this routing
        # read the parsed dataclass, whose `rules` defaults to {}, and could not tell
        # "clear these" from "I did not come here about rules". It wiped them.
        check("a save that says nothing about rules leaves them alone",
              load_stop_filters(prof).min_employer_rating == 3.6)

        # ── the wizard prefills from the file it writes ────────────────────
        info = client.get("/api/v1/profiles/reach", headers=headers).json()
        check("the profile route reports stop rules from filters.json, so a re-run prefills "
              "from the same place the save wrote",
              "acme" in (info.get("stop_companies") or []))
        check("…and the employer rating with them", info.get("min_employer_rating") == 3.6)

        # ── clearing a rule actually clears it ─────────────────────────────
        # "Enter = no filter" in the CLI, an emptied box in the GUI. Both used to
        # leave whatever was already on disk in place, because the writer read None
        # as "caller said nothing" — so a filter could be added and never removed.
        save("reach", rules={"stop": [], "penalize": [], "min_employer_rating": None})
        filters = load_stop_filters(prof)
        check("clearing `Never apply to` removes the rule rather than keeping the old one",
              not filters.title_keywords and not filters.companies)
        check("clearing `Min employer rating` removes it too", filters.min_employer_rating is None)

        # ── an omitted rule is still left alone ────────────────────────────
        patch_filters_json(prof, min_match=75)
        patch_filters_json(prof, stop_companies=["acme"])
        check("a writer that says nothing about min_match does not touch it",
              load_stop_filters(prof).min_match == 75)

        # ── an empty save may not empty a real profile ─────────────────────
        r = save("reach", skills=[], identity={})
        check(f"a save with no substance is refused for a profile that has some (HTTP {r.status_code})",
              r.status_code == 409)

        # ── the manifest: what the runtime reads is a closed set ───────────
        # candidate.json is deliberately absent from it. If a future change makes
        # the engine read the wizard's answer file directly, this fails and the
        # question gets asked out loud instead of being discovered in a live run.
        engine_dir = Path(__file__).parent.parent
        runtime_readers = list((engine_dir / "adapters").rglob("*.py")) + [
            engine_dir / "core" / "llm_agent.py",
            engine_dir / "llm_cover.py",
            engine_dir / "utils" / "filters.py",
        ]
        offenders = [p.name for p in runtime_readers
                     if p.exists() and "candidate.json" in p.read_text(encoding="utf-8")]
        check("no runtime module reads candidate.json — it is the wizard's saved answers, "
              f"not a source of configuration (offenders: {offenders or 'none'})",
              not offenders)

        # ── Semantic stop categories ──────────────────────────────────────
        # The audit's gap #7 in its final form. Nothing in the app ever wrote
        # job_preferences.md, so a profile built there declared no semantic
        # categories at all — and once a block has to name a declared category,
        # that means the semantic tier does not exist for those users. The
        # category now lives on a keyed line in candidate.md: the file the model
        # already receives, so the list it reads and the list its answer is
        # checked against are one line, not two copies that can disagree.
        from onboarding.profile_frame import KEY_OWNERS
        from onboarding.resume_parser import ResumeParser, ResumeData

        cat_dir = root / "categories"
        cat_dir.mkdir(parents=True, exist_ok=True)
        cat_md = ResumeParser(None).to_md(ResumeData(
            identity={"name": "T"}, rules={"stop_categories": ["example_category", "second_category"]}))
        (cat_dir / "candidate.md").write_text(cat_md, encoding="utf-8")

        check("a saved profile writes the stop_categories line",
              "stop_categories: example_category, second_category" in cat_md)
        check("and the engine reads it back, normalised",
              load_stop_filters(cat_dir).categories == ["example_category", "second_category"])
        # `Career Profile` until 2026-08-25. The section split by readership once the
        # human layer was retired out of it: the hard refusals went to Constraints,
        # addressed to nobody, and the soft one to Preferences, addressed to the
        # letter writer. What this pins is unchanged — ONE section owns the key, so a
        # hand-written line has exactly one place to land.
        check("the frame owns the key, so a hand-written line lands in one place",
              KEY_OWNERS.get("stop_categories") == "Constraints")
        check("and the soft refusal is owned separately, because it is read by "
              "someone else entirely",
              KEY_OWNERS.get("not_looking_for") == "Preferences")

        (cat_dir / "job_preferences.md").write_text(
            "stop_categories:\n  - third_category\n", encoding="utf-8")
        check("a second file no longer widens the block vocabulary — it was merged "
              "by union, so a category removed from candidate.md was not removed",
              load_stop_filters(cat_dir).categories == ["example_category", "second_category"])

        bare = root / "declares-nothing"
        bare.mkdir(parents=True, exist_ok=True)
        check("a profile declaring nothing declares nothing — no invented defaults",
              load_stop_filters(bare).categories == [])
    finally:
        api.PROFILES_DIR = real_profiles_dir
        shutil.rmtree(root, ignore_errors=True)

    print()
    print(f"{'❌ ' + str(len(failures)) + ' failed' if failures else '✅ all passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
