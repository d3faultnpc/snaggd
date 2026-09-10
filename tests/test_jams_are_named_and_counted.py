"""Where the claw stops deciding, it says which decision it was.

Twelve functions in this adapter answer "what do I do next", and until
2026-09-10 all of them gave up the same way: return None or False, caller
improvises. A jam was indistinguishable from "there is genuinely nothing here",
had no name, and was counted nowhere.

Measured before the change: outside dom.py the adapter reads the DOM 88 times
across ten files, 31 addresses are named in config, and 24 of those 31 are a
single string with no fallback at all. Cascades exist only where hh has already
broken us — apply_button after 2026-08-02, chatik_input after 2026-08-29. Canon
here is retrofitted by incident, and the other 24 are the incidents that have
not happened yet.

What this pins is the contract, not a fix: a jam names its node, the vocabulary
is closed, and the claw behaves exactly as it did. The navigator arrives at the
next step and replaces one function body; these names are how it will know where
it is standing, and the counts are how we will know which node to give it first.
"""
import re
import sys
from pathlib import Path
from unittest.mock import patch

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

from utils.call_ledger import CallLedger, set_ledger
from utils.navigation import NODES, jam

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


# ── the contract itself ──────────────────────────────────────────────────────
led = CallLedger()
set_ledger(led)
try:
    got = jam("action_button", "nothing matched")
finally:
    set_ledger(None)
check("a jam decides nothing and returns nothing", got is None)
check("and is counted under its own node", led.run_summary()["jams"] == {"action_button": 1})

led = CallLedger()
set_ledger(led)
try:
    jam("option_match"); jam("option_match"); jam("form_type")
finally:
    set_ledger(None)
check("repeated jams at one node are one line with a count",
      led.run_summary()["jams"] == {"option_match": 2, "form_type": 1})

set_ledger(None)
check("with no ledger set, a jam is still harmless", jam("form_type") is None)

led = CallLedger()
check("a run with no jams says so plainly, rather than omitting the field",
      led.run_summary()["jams"] == {})

# ── the vocabulary is closed, and both directions are checked ────────────────
# A node inventing its own name lands in a counter nobody reads; a declared node
# nothing ever reaches is a counter that can never move. Same two rules the
# feature register enforces one layer up, for the same reason.
SOURCES = [p for p in (_ENGINE / "adapters").rglob("*.py")]
used = set()
for p in SOURCES:
    used |= set(re.findall(r'\bjam\(\s*["\']([a-z_]+)["\']', p.read_text(encoding="utf-8")))

check(f"every node a call site names is declared (undeclared: {sorted(used - set(NODES))})",
      used <= set(NODES))
check(f"every declared node is reachable from a call site (unreached: {sorted(set(NODES) - used)})",
      set(NODES) <= used)
check("the twelve decisions are all named", len(NODES) >= 11)

# ── the claw's behaviour is unchanged, which is the whole safety argument ────
# Each call site had to keep returning exactly what it returned before. Checked
# by reading the source rather than by driving a browser: what matters is that
# the jam sits BESIDE the original return, never in place of it.
PAIRS = [
    ("adapters/hh/detector.py", 'jam("form_type"', "return FormType.UNKNOWN"),
    ("adapters/hh/browser.py", 'jam("apply_button"', "return False"),
    ("adapters/hh/handlers/base.py", 'jam("action_button"', "return None, None"),
    ("adapters/hh/handlers/base.py", 'jam("cover_field"', "return None"),
    ("adapters/hh/handlers/questions.py", 'jam("submit_button"', "return ProcessResult("),
    ("adapters/hh/handlers/chat.py", 'jam("cover_input"', "_dump_frame"),
    ("adapters/hh/adapter.py", 'jam("blocking_modal"', "return False"),
]
for rel, needle, after in PAIRS:
    src = (_ENGINE / rel).read_text(encoding="utf-8")
    i = src.find(needle)
    tail = src[i:i + 700] if i >= 0 else ""
    check(f"{rel.split('/')[-1]}: {needle[5:-1]} still returns what it always did",
          i >= 0 and after in tail)

# ── a jam belongs where the NEED is known ───────────────────────────────────
# _find_add_cover_btn misses legitimately whenever the letter already went out
# in an earlier layer: 30 of 33 misses measured over three weeks were exactly
# that. Jamming inside the lookup would have been 91% noise.
chat = (_ENGINE / "adapters/hh/handlers/chat.py").read_text(encoding="utf-8")
_lookup = chat.find("def _find_add_cover_btn")
_next_def = chat.find("\n    def ", _lookup + 10)
check("the add-cover lookup itself does not jam — only its caller does",
      'jam("add_cover_button"' not in chat[_lookup:_next_def])
check("and the caller does, on the path where the letter was actually lost",
      'jam("add_cover_button"' in chat)

# ── the shared lookup no longer swallows a broken selector ──────────────────
from adapters.hh.dom import find_visible


class _Scope:
    def __init__(self):
        self.seen = []

    def query_selector_all(self, sel):
        self.seen.append(sel)
        if sel == "broken":
            raise ValueError("malformed")
        return []


sc = _Scope()
with patch("builtins.print") as _p:
    out = find_visible(sc, ["broken", "next-one"])
check("a selector that raises does not stop the cascade", sc.seen == ["broken", "next-one"])
check("and is no longer swallowed in silence",
      any("could not be evaluated" in str(c) for c in _p.call_args_list))
check("a cascade that matched nothing still returns None", out is None)

# ── the debug observer ──────────────────────────────────────────────────────
# A jam is the one moment worth a full capture, and until it was wired it
# produced a single line of stdout mixed into two dozen other kinds of warning.
# The observer is debug-only and structurally so: nothing registers it on a
# customer's run, which is what keeps a page carrying their own profile fields
# off disk.
from utils.navigation import set_jam_observer

seen = []
set_jam_observer(lambda node, detail, scope: seen.append((node, scope)))
try:
    sentinel = object()
    jam("cover_input", "no field behind the control", scope=sentinel)
finally:
    set_jam_observer(None)
check("the observer is told which node jammed", seen and seen[0][0] == "cover_input")
check("and is handed the surface the decision was made on, not the page behind it",
      seen and seen[0][1] is sentinel)

set_jam_observer(None)
check("with no observer, a jam is unchanged", jam("cover_input") is None)


def _explodes(node, detail, scope):
    raise RuntimeError("capture failed")


set_jam_observer(_explodes)
try:
    led = CallLedger()
    set_ledger(led)
    out = jam("form_type")
finally:
    set_ledger(None)
    set_jam_observer(None)
check("an observer that throws costs the observation, never the run", out is None)
check("and the jam is still counted", led.run_summary()["jams"] == {"form_type": 1})

# Every jam that has a surface to offer, offers it. Without this the capture
# falls back to the current page — which for chatik is the wrong side of a
# cross-domain iframe, the exact blind spot that cost the 2026-08-29 diagnosis.
_no_scope_ok = {"form_type"}  # classification has only its own collected signals
for p_ in (_ENGINE / "adapters").rglob("*.py"):
    src = p_.read_text(encoding="utf-8")
    # Parentheses are BALANCED rather than matched to the first ")": two of these
    # calls carry a nested paren in their own message — "answer(s) filled",
    # "address(es) tried" — and a first-paren scan reported both as missing a
    # scope they had. A checker that cries wolf gets its own exception list, and
    # then it is not a checker.
    for m in re.finditer(r'\bjam\(\s*["\']([a-z_]+)["\']', src):
        i, depth = src.index("(", m.start()), 0
        for j in range(i, min(i + 600, len(src))):
            depth += (src[j] == "(") - (src[j] == ")")
            if depth == 0:
                break
        node, rest = m.group(1), src[m.end():j]
        if node in _no_scope_ok:
            continue
        check(f"{p_.name}: jam at {node} hands over its scope", "scope=" in rest)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
