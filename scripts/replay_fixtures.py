#!/usr/bin/env python3
"""Re-score saved vacancies offline and say what moved.

A prompt change moves scores and blocks at the same time — that is not a theory,
it was measured: an attempt to lift the arithmetic out of the scoring prompt took
zero-scores from 3/6 to 0/6 and dropped stop-category blocking from 4/4 to 2/4 in
the same edit, and the second half went unnoticed until the run was read by hand.
Nothing in the loop could have caught it, because there was no way to ask the same
question twice.

This asks it twice. Fixtures are vacancy texts saved with whatever the pipeline
answered about them; a run re-scores every one and prints a diff against a stored
baseline.

Two properties this depends on, both easy to lose:

  The profile is COPIED. llm_cover writes its score cache into the profile
  directory, so a run against the live one would both pollute it and turn the
  next run into a cache hit — a comparison the model never took part in, showing
  perfect agreement.

  The baseline is TODAY'S answer, not the one stored in the fixture. Fixtures
  carry what the pipeline said when the case was noticed, but candidate.md has
  changed since; treating that as ground truth would show differences that have
  nothing to do with the prompt.

Not a test and deliberately not named like one: tests/ runs in CI on every push,
and every run here spends real model calls.

    scripts/replay_fixtures.py --fixtures ~/.snaggd-fixtures/2026-08-21 \
                               --profile data/profiles/pm --out baseline.json
    scripts/replay_fixtures.py --fixtures ... --profile ... --against baseline.json
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm_cover import LLMCover  # noqa: E402

# What a re-score is compared on. Deliberately not the whole answer: prose fields
# move on wording alone, and a diff that lights up every run gets ignored.
_COMPARED = ("score", "stop_match", "stop_basis", "non_compensable")


def _load_fixtures(d: Path) -> list:
    out = []
    for f in sorted(d.glob("*.json")):
        payload = json.loads(f.read_text(encoding="utf-8"))
        if payload.get("vacancy_text"):
            out.append(payload)
    return out


def _score_one(cover: LLMCover, text: str) -> dict:
    ok = cover.score(text)
    return {
        "ok": bool(ok),
        "score": cover.last_score,
        "stop_match": cover.last_stop_match,
        "stop_basis": cover.last_stop_basis,
        "stop_evidence": cover.last_stop_evidence,
        "axes": dict(cover.last_axes or {}),
        "non_compensable": list(cover.last_non_compensable or []),
        "matched_skills": list(cover.last_matched_skills or []),
        "matched_skills_dropped": cover.last_matched_skills_dropped,
        "scoring_format": cover.last_scoring_format,
        "signals": list(cover.last_signals or []),
        "call_meta": cover.last_call_meta,
        "error": cover.last_score_error,
    }


def spread(fixtures_dir: Path, profile_dir: Path, passes: int) -> dict:
    """Score every fixture `passes` times and report how much each one wanders.

    Structured calls run at temperature 0, which is often read as "the same
    input gives the same answer". It does not: the first two runs of this
    harness disagreed on a third of the set, and one vacancy came back 0 once
    and 85 the next time — the same text, profile, prompt and model. A model
    served across backends has no obligation to be reproducible, and a
    mixture-of-experts one especially not.

    That matters before any prompt is edited, not after: an A/B on single runs
    measures the wander, not the change. This is how the floor gets known.
    """
    fixtures = _load_fixtures(fixtures_dir)
    tmp, work, cover = _workspace(profile_dir)
    samples = {fx["vacancy_id"]: [] for fx in fixtures}
    kinds = {fx["vacancy_id"]: fx.get("kind") for fx in fixtures}

    for p in range(1, passes + 1):
        print(f"\n  pass {p}/{passes}")
        for fx in fixtures:
            # In-memory cache cleared between passes: without this every pass
            # after the first answers itself, and the spread reads as zero.
            cover.cache.clear()
            got = _score_one(cover, fx["vacancy_text"])
            samples[fx["vacancy_id"]].append(got["score"] if got["ok"] else None)
            print(f"    {fx.get('kind','?'):12} {fx['vacancy_id']}  "
                  f"{got['score'] if got['ok'] else 'ERR'}", flush=True)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n  {'kind':12} {'id':>10}  samples                 spread")
    worst = 0
    for vid, vals in samples.items():
        real = [v for v in vals if v is not None]
        rng = (max(real) - min(real)) if len(real) > 1 else 0
        worst = max(worst, rng)
        flag = "  ←" if rng >= 10 else ""
        print(f"  {kinds[vid] or '?':12} {vid:>10}  {str(vals):22}  {rng:>3}{flag}")
    print(f"\n  widest spread on one vacancy: {worst} points")
    return {"samples": samples, "worst": worst}


def _workspace(profile_dir: Path):
    # A missing profile used to produce an empty workspace and a full run: every
    # fixture scored against no candidate at all, every answer well-formed, every
    # number meaningless. It happened on 2026-08-22 by pointing --profile at a path
    # that does not exist inside a git worktree, where data/profiles is ignored and
    # only the main checkout has it. Nothing in the output said so.
    #
    # This is the third time in this project that a measurement turned out to be
    # about the harness. The run stops instead.
    if not (profile_dir / "candidate.md").exists():
        raise SystemExit(
            f"no candidate.md in {profile_dir} — there is nothing to score against, "
            f"and a run without it would answer every fixture and mean nothing. "
            f"(data/profiles is gitignored: inside a worktree, pass the main "
            f"checkout's absolute path.)")
    tmp = Path(tempfile.mkdtemp(prefix="replay-"))
    work = tmp / profile_dir.name
    work.mkdir(parents=True)
    for needed in ("candidate.md", "candidate.json", "filters.json"):
        src = profile_dir / needed
        if src.exists():
            shutil.copy2(src, work / needed)
    return tmp, work, LLMCover(data_dir=work)


def run(fixtures_dir: Path, profile_dir: Path) -> dict:
    fixtures = _load_fixtures(fixtures_dir)
    if not fixtures:
        raise SystemExit(f"no fixtures in {fixtures_dir}")

    # Copied, not used in place — see the module docstring. Also means the score
    # cache starts empty, so every fixture costs a real call and none of them
    # answer each other.
    # Only what scoring reads — see _workspace(). Copying the directory wholesale
    # would drag the apply log and its backups along: tens of megabytes per run,
    # and a copy of the person's application history sitting in /tmp for no reason.
    tmp, work, cover = _workspace(profile_dir)
    results, tokens_in, tokens_out = {}, 0, 0

    for i, fx in enumerate(fixtures, 1):
        vid = fx["vacancy_id"]
        print(f"  [{i}/{len(fixtures)}] {fx.get('kind','?'):12} {vid}", flush=True)
        got = _score_one(cover, fx["vacancy_text"])
        got["kind"] = fx.get("kind")
        got["noticed_as"] = fx.get("expected", {})
        results[vid] = got
        meta = got.get("call_meta") or {}
        tokens_in += meta.get("tokens_prompt") or 0
        tokens_out += meta.get("tokens_completion") or 0

    shutil.rmtree(tmp, ignore_errors=True)
    return {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "profile": profile_dir.name,
        "model": cover._agent.model if cover._agent else None,
        "tokens_prompt_total": tokens_in,
        "tokens_completion_total": tokens_out,
        "results": results,
    }


def diff(before: dict, after: dict) -> int:
    moved = 0
    print(f"\n  base {before.get('ran_at')}  →  now {after.get('ran_at')}")
    for vid, now in after["results"].items():
        was = before["results"].get(vid)
        if was is None:
            print(f"  +  {vid}  new fixture, nothing to compare")
            continue
        changes = [(f, was.get(f), now.get(f)) for f in _COMPARED if was.get(f) != now.get(f)]
        if not changes:
            continue
        moved += 1
        print(f"  ~  {now.get('kind','?'):12} {vid}")
        for field, a, b in changes:
            print(f"       {field}: {a!r} → {b!r}")
    print(f"\n  {moved} of {len(after['results'])} moved")
    return moved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True, type=Path)
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--out", type=Path, help="write this run as a baseline")
    ap.add_argument("--against", type=Path, help="compare this run to a stored baseline")
    ap.add_argument("--repeat", type=int, default=0,
                    help="score every fixture N times and report the spread instead")
    args = ap.parse_args()

    if args.repeat:
        print(f"measuring spread over {args.repeat} passes — {args.fixtures}")
        spread(args.fixtures, args.profile, args.repeat)
        return 0

    print(f"replaying {args.fixtures} against profile {args.profile.name}")
    result = run(args.fixtures, args.profile)
    print(f"\n  tokens: {result['tokens_prompt_total']} in / "
          f"{result['tokens_completion_total']} out  ({result['model']})")

    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  baseline written: {args.out}")
    if args.against:
        diff(json.loads(args.against.read_text(encoding="utf-8")), result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
