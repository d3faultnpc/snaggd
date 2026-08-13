"""
The data directory is a parameter, never ambient state.

CONFIG.data_dir is resolved once, at import, from the DATA_DIR env var. That
model only holds for a process serving exactly one profile — which the CLI is
and the API is not: api.py resolves the active profile per request. So any
runtime component that reaches for CONFIG.data_dir instead of the data_dir it
was handed reads whatever the flat legacy directory happens to contain.

That is not hypothetical. On 2026-08-13 three separate instances were live at
once: the apply loop read stop filters (and therefore the match threshold) from
the legacy dir, so a 72%% vacancy was applied to under a 75%% setting; and the
chat and modal handlers built their LLM agent at import time with no data_dir,
so every screening answer and HR reply was grounded in a candidate.md two months
stale — claiming a job the candidate had already left — while cover letters, on
the correct path, used the right file. Same person, two sets of facts, one
employer.

Constructors now require data_dir, so a forgotten argument raises instead of
reading someone else's profile. This test guards the other half: that no runtime
module quietly reintroduces the ambient default. A rule with no mechanism gets
broken here — this is the mechanism.

Allowed to mention CONFIG.data_dir:
  config.py     — owns it
  main.py       — the CLI entry point, where "one profile per process" is true
                  and resolution legitimately happens
  scripts/      — one-off developer tools, run by hand against one profile
  onboarding/   — the CLI wizard, whose 20 uses are a known debt scheduled for
                  its own pass; listed here so the number can only shrink

Anything else — adapters, core, handlers, llm_cover, api, utils — must take the
directory as an argument.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

ALLOWED_PREFIXES = ("config.py", "main.py", "scripts/", "onboarding/", "tests/")

# Every use inside onboarding/ that exists today. The wizard pass must drive this
# to zero; until then a NEW one still fails the test, because the count is pinned.
WIZARD_BUDGET = 20

SKIP_DIRS = {"venv", ".git", ".claude", "debug_screenshots", "__pycache__", "sandbox"}

_USE = re.compile(r"CONFIG\.data_dir")


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def collect():
    """Returns (violations, wizard_uses) — real code uses only, comments ignored."""
    violations, wizard_uses = [], []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _USE.search(line) or _is_comment(line):
                continue
            if rel.startswith("onboarding/"):
                wizard_uses.append(f"{rel}:{n}")
            elif not rel.startswith(ALLOWED_PREFIXES):
                violations.append(f"{rel}:{n}  {line.strip()}")
    return violations, wizard_uses


failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        failures += 1
        if detail:
            print(detail)


print("data_dir is explicit, not ambient:")

violations, wizard_uses = collect()

check(
    "no runtime module reads CONFIG.data_dir",
    not violations,
    "\n".join(f"      {v}" for v in violations)
    + "\n      → take data_dir as an argument; the caller knows the active profile.",
)

check(
    f"onboarding/ budget not exceeded ({len(wizard_uses)} of {WIZARD_BUDGET} allowed)",
    len(wizard_uses) <= WIZARD_BUDGET,
    "\n".join(f"      {u}" for u in wizard_uses)
    + "\n      → the wizard's ambient uses are a known debt; do not add more.",
)

# The constructors themselves: a missing data_dir must fail loudly rather than
# resolve to the legacy directory. Checked by signature so this needs no API key
# and no playwright — inspecting the parameter is the whole contract.
import inspect

for mod_path, cls_name in [
    ("core.llm_agent", "LLMAgent"),
    ("llm_cover", "LLMCover"),
]:
    mod = __import__(mod_path, fromlist=[cls_name])
    sig = inspect.signature(getattr(mod, cls_name).__init__)
    param = sig.parameters.get("data_dir")
    check(
        f"{cls_name}.__init__ requires data_dir (no default)",
        param is not None and param.default is inspect.Parameter.empty,
        f"      got default: {param.default!r}" if param else "      no data_dir parameter at all",
    )

total = 4
print(f"\n{total - failures}/{total} passed")
sys.exit(1 if failures else 0)
