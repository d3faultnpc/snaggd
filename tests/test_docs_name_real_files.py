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
import subprocess
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
# The documents this REPOSITORY carries, asked of git rather than of the filesystem.
#
# Until 2026-08-25 this walked the tree with rglob and an exclusion list, and the two
# things it swept up made the local answer differ from CI's without either being wrong:
#
#   · gitignored local files — a three-month-old `data/README.md` describing the flat
#     pre-profile era, and DEVLOG.md. Neither is shipped, neither is in CI, so the
#     test failed on one machine and passed on the build. A claim this repository
#     does not carry is not a claim this repository makes.
#   · nested worktrees under .claude/worktrees — another checkout of this same repo at
#     another commit, so the test was reporting a different branch's docs as this
#     branch's ghosts. Same class as the corpus test reading debug_screenshots: the
#     instrument's scope had grown to include something that is not the product.
#
# Asking git also means the exclusion list stops being a list to maintain: .gitignore
# already is that list, and it is the one the build obeys.
_ls = subprocess.run(["git", "-C", str(_ROOT), "ls-files", "-z", "*.md"],
                     capture_output=True, text=True)
if _ls.returncode != 0:
    print("  ❌ cannot ask git what this repository carries — this test needs a checkout")
    sys.exit(1)
# `.exists()` because `git ls-files` lists what the INDEX holds, and a file deleted
# from the working tree is still in it until the deletion is staged. A document that
# is not on disk makes no claim about anything; without this the test crashed on the
# first commit that removed a prompt.
_docs = [d for d in (_ROOT / p for p in _ls.stdout.split("\0") if p)
         if d.name not in _EXEMPT
         and "working-notes" not in d.relative_to(_ROOT).parts
         and d.exists()]

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
