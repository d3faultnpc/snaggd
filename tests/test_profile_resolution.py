"""
Unit tests for profiles.resolve_profile() — the profile selection law.
No LLM calls, no real data/ dir — PROFILES_DIR is monkeypatched to a temp dir per case.
"""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import profiles as _profiles_module
from profiles import ProfileError, resolve_profile


def _make_profile(base: Path, name: str) -> None:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidate.md").write_text("# test", encoding="utf-8")


def run_case(label, setup_names, requested, expect):
    """expect: the profile name expected back, or the string "error" for ProfileError."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for name in setup_names:
            _make_profile(base, name)
        _profiles_module.PROFILES_DIR = base
        try:
            result = resolve_profile(requested, exit_on_error=False)
            ok = result == expect
            print(f"  {'✅' if ok else '❌'} {label}: got {result!r} (expected {expect!r})")
            return ok
        except ProfileError as e:
            ok = expect == "error"
            print(f"  {'✅' if ok else '❌'} {label}: raised ProfileError({e!r}) (expected {expect!r})")
            return ok


cases = [
    ("zero profiles, no request",             [],                 None,  "error"),
    ("one profile, no request (auto-select)", ["pm"],             None,  "pm"),
    ("two profiles, no request (ambiguous)",  ["pm", "support"],  None,  "error"),
    ("two profiles, explicit valid name",     ["pm", "support"],  "pm",  "pm"),
    ("one profile, explicit matching name",   ["pm"],             "pm",  "pm"),
    ("explicit name not found",               ["pm"],             "xyz", "error"),
]

_orig_dir = _profiles_module.PROFILES_DIR
passed = 0
for label, setup_names, requested, expect in cases:
    if run_case(label, setup_names, requested, expect):
        passed += 1
_profiles_module.PROFILES_DIR = _orig_dir

print(f"\n{passed}/{len(cases)} passed")
assert passed == len(cases), f"FAILED: {len(cases) - passed} test(s) failed"
print("All tests passed.")
