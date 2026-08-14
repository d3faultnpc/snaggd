"""
Regression tests for POST /api/v1/profiles/{name}/candidate-md.

What they pin down (2026-08-14): candidate.md had no writer other than the
wizard. The Settings profile pane offered a hand-edit box — "edit by hand
below", hint `esc · save` — whose blur handler only closed the editor. The edit
went into component state, re-rendered as though it had been saved, and was
replaced by the file on the next fetch. candidate.md is what every agent grounds
on, and the only place to put a preference the schema has no field for, so the
pane was offering the most consequential edit in the product and dropping it.

The endpoint runs against a temporary profile tree: api.PROFILES_DIR resolves
from app_paths.get_data_root() at import, NOT from DATA_DIR, so it must be
pointed elsewhere explicitly or this test writes into the developer's own live
profile. The assertion below that it is a temp path is not decoration.

The app's sidecar fork (snaggd-app/sidecar-ext/api.py) carries the same handler
verbatim; this is the copy that gets the test.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# A profile whose value is in the keys the schema does not have — the shape the
# whole 2026-08-14 sprint is about.
PROFILE_MD = """# Fintech PM

## Career Profile
role_type: builder
not_looking_for: process_management, pmm, outsource

## Desired Salary
default: 120 000+ net
note: input/modal → number only

## Skills
- product discovery
"""


def run():
    os.environ.setdefault("LLM_API_KEY", "test")
    os.environ["API_KEY"] = "test-key-for-candidate-md"

    import api
    from config import CONFIG
    from fastapi.testclient import TestClient

    root = Path(tempfile.mkdtemp(prefix="snaggd-candidate-md-"))
    real_profiles_dir = api.PROFILES_DIR
    api.PROFILES_DIR = root / "profiles"
    assert api.PROFILES_DIR != real_profiles_dir, "refusing to run against the live profile tree"

    prof = api.PROFILES_DIR / "pm"
    prof.mkdir(parents=True)
    (prof / "candidate.md").write_text(PROFILE_MD, encoding="utf-8")

    client = TestClient(api.app)
    headers = {"X-API-Key": CONFIG.api_key}
    failures = []

    def check(label, condition):
        print(f"  {'✅' if condition else '❌'} {label}")
        if not condition:
            failures.append(label)

    def post(payload, name="pm", auth=True):
        return client.post(f"/api/v1/profiles/{name}/candidate-md",
                           headers=headers if auth else {}, json=payload)

    try:
        edited = PROFILE_MD + "\n## My own section\nhand-written line\n"
        r = post({"candidate_md": edited})
        check(f"a hand edit is written to disk (HTTP {r.status_code})", r.status_code == 200)
        on_disk = (prof / "candidate.md").read_text(encoding="utf-8")
        check("the hand-written section is there", "hand-written line" in on_disk)
        check("the keys the schema has no field for are untouched",
              "not_looking_for: process_management" in on_disk and "note: input/modal" in on_disk)
        check("the previous version was backed up first",
              (prof / ".backups").is_dir() and any((prof / ".backups").iterdir()))
        check("candidate.json is not created or touched — candidate.md is the profile, "
              "the JSON is the wizard's saved answers",
              not (prof / "candidate.json").exists())

        # A cleared textarea is the hand-edit shape of the 2026-08-11 blank-wizard
        # wipe. Same rule: may add, may change, may not empty.
        r = post({"candidate_md": "# Fintech PM\n\n## Skills\n"})
        check(f"a save that empties the profile is refused (HTTP {r.status_code})", r.status_code == 409)
        check("and nothing was written when it was refused",
              "hand-written line" in (prof / "candidate.md").read_text(encoding="utf-8"))

        r = post({"candidate_md": "# Fintech PM\n\n## Skills\n", "overwrite": True})
        check(f"…unless the caller says so explicitly (HTTP {r.status_code})", r.status_code == 200)

        r = post({"candidate_md": "anything"}, name="no-such-profile")
        check(f"an unknown profile 404s rather than creating one (HTTP {r.status_code})",
              r.status_code == 404)

        r = post({"candidate_md": "anything"}, auth=False)
        check(f"the endpoint is behind the same API key as the rest (HTTP {r.status_code})",
              r.status_code == 401)

        r = post({"candidate_md": "# Role\n\n## Skills\n- one\n"})
        check("a saved file always ends with a newline",
              (prof / "candidate.md").read_text(encoding="utf-8").endswith("\n"))
    finally:
        api.PROFILES_DIR = real_profiles_dir
        shutil.rmtree(root, ignore_errors=True)

    print()
    print(f"{'❌ ' + str(len(failures)) + ' failed' if failures else '✅ all passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
