"""A doc describing today may not name a profile file the code never touches.

Claims in this repository's own docs have been false often enough to be a pattern
rather than an accident. The last instance: `job_preferences.md` was deleted from the
code on 2026-08-21, and on 2026-08-22 nine places still described it as current —
including docs/status_codes.md, which told a person to fix a blocked vacancy by
editing a file that no longer exists.

Fixing them again is not a mechanism. This is: the set of filenames the code actually
reads or writes inside a profile directory is derived from the source, and any
current-state doc naming a profile file outside that set fails.

CHANGELOG.md is exempt and must stay exempt — it records what was true when it was
written, and rewriting history to match today is how a changelog stops being one.
"""
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


# Filenames the code names as living in a profile directory. Read from the source so
# the set cannot drift from it: a file the code stops touching leaves here on its own.
_PROFILE_FILE_RE = re.compile(r'["\'](\w[\w.-]*\.(?:md|json|txt))["\']')
_code_named: set = set()
for py in list(_ROOT.rglob("*.py")):
    if any(part in (".git", "venv", "__pycache__", "tests")
           for part in py.relative_to(_ROOT).parts):
        continue
    _code_named |= set(_PROFILE_FILE_RE.findall(py.read_text(encoding="utf-8")))

# Docs that describe the present. CHANGELOG records the past on purpose.
_EXEMPT = {"CHANGELOG.md"}
# Relative to the root, always. Computed on the absolute path this excluded every
# file in the repository, because the checkout itself sits under a directory called
# `worktrees` — and the guard passed on an empty set, which is why "is there anything
# to check" is the first assertion here rather than an afterthought.
_docs = [p for p in _ROOT.rglob("*.md")
         if not any(part in (".git", "venv", "node_modules", "working-notes")
                    for part in p.relative_to(_ROOT).parts)
         and p.name not in _EXEMPT]

check(f"the code names profile files, so there is something to check against "
      f"({len(_code_named)} found)", len(_code_named) >= 5)
check(f"there are current-state docs to check ({len(_docs)} found)", len(_docs) >= 3)

# A claim about profile contents, judged per LINE rather than per document. Checking
# the whole file caught CONTEXT.md naming `L2_tasks.md` — a memory file, mentioned
# hundreds of lines away from anything about profiles. Proximity is the claim.
_PROFILE_PATH_RE = re.compile(r"data/(?:profiles/[^/\s]+/)?([\w.-]+\.(?:md|json|txt))")
_ghosts = []
for doc in _docs:
    for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        for name in _PROFILE_PATH_RE.findall(line):
            if name not in _code_named:
                _ghosts.append(f"{doc.relative_to(_ROOT)}:{lineno} → {name}")

_ghosts = sorted(set(_ghosts))
check(f"no current-state doc names a profile file the code never touches "
      f"(ghosts: {_ghosts or 'none'})", not _ghosts)

# The specific one this file was written for, pinned by name so its return is loud.
_still = sorted(str(d.relative_to(_ROOT)) for d in _docs
                if "job_preferences" in d.read_text(encoding="utf-8"))
check(f"job_preferences.md is not described as current anywhere (found in: "
      f"{_still or 'nowhere'})", not _still)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
