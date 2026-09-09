"""A cover letter that was delivered says so on the record, whichever route sent it.

hh offers three places a cover can actually be sent, and it picks: the response
modal (where the letter is mandatory), chatik, and the post-apply cover form.
Only chatik ever left evidence. On the live profile, 921 records carried
cover_length on exactly the 396 that went through chatik, and on none of the 54
that went through the modal.

Not a forgotten field — a structural one. hh_modal.py returns is_terminal=False
so the loop continues to chatik for the terminal status, and adapter.py builds
the record from `loop_result.details`, i.e. from the LAST layer. The delivery
happened one layer earlier and was thrown away with the rest of that layer's
details.

Downstream, the app read `status === 'applied_no_cover'` — one record in those
921 — while the engine writes `applied_via_chat_no_cover`, of which there are
31. So History printed "cover letter sent." under all 32 applications that had
none, 6.3% of everything it called applied.

What is pinned here is the fact, not the status: which route delivered, and its
evidence, carried across layers. `goal_reached` and every status string are left
exactly as they were on purpose — they are next, once the instrument has
measured how often they disagree with this.
"""
import re
import sys
from pathlib import Path
from unittest.mock import patch

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        from adapters.hh.handlers.base import COVER_ROUTES, cover_delivery_of

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


# ── the fact a layer declares ────────────────────────────────────────────────
check("a layer that delivered nothing declares nothing",
      cover_delivery_of({'button_text': 'Откликнуться'}) is None)
check("no details at all is not a delivery", cover_delivery_of(None) is None)

for route in COVER_ROUTES:
    got = cover_delivery_of({'cover_delivered': route, 'cover_length': 7, 'cover_text': 'letter!'})
    check(f"route {route!r} is carried with its evidence",
          got == {'cover_delivered': route, 'cover_length': 7, 'cover_text': 'letter!'})

# Fails closed on the CLAIM. A route nobody can name silently asserting that a
# letter reached an employer is the direction worth being strict about.
check("a route this module cannot name is not a delivery",
      cover_delivery_of({'cover_delivered': 'chatik', 'cover_length': 7}) is None)
check("a truthy non-route is not a delivery",
      cover_delivery_of({'cover_delivered': True}) is None)
check("only the delivery keys are carried — a layer's own details do not ride along",
      cover_delivery_of({'cover_delivered': 'modal', 'cover_length': 3,
                         'button_text': 'Далее'}) == {'cover_delivered': 'modal',
                                                      'cover_length': 3})

# ── every route that can deliver, names itself and brings evidence ───────────
# Source-level, deliberately: reaching these lines for real needs a live page and
# an employer's form. What can be checked without one is that no route is missing
# from the set, which is the failure that produced this test.
HANDLERS = {
    'modal': 'adapters/hh/handlers/hh_modal.py',
    'chat': 'adapters/hh/handlers/chat.py',
    'cover_only': 'adapters/hh/handlers/cover_only.py',
}
check("every route in COVER_ROUTES has a handler that claims it",
      set(HANDLERS) == set(COVER_ROUTES))

for route, rel in HANDLERS.items():
    src = (_ENGINE / rel).read_text(encoding="utf-8")
    check(f"{rel} declares route {route!r}", f"'cover_delivered': '{route}'" in src)
    check(f"{rel} records the length beside the claim", "'cover_length': len(cover_letter)" in src)

# Nothing else may claim a delivery. questions.py answers cover-shaped fields
# through the generic fill_form path — that is not hh's own cover mechanism, and
# session 56 already removed the status that used to pretend otherwise.
for rel in ('adapters/hh/handlers/questions.py', 'adapters/hh/handlers/test_form.py',
            'adapters/hh/handlers/salary.py'):
    src = (_ENGINE / rel).read_text(encoding="utf-8")
    check(f"{rel} claims no delivery", "'cover_delivered'" not in src)

# ── the loop carries it across layers, and merges it onto what it returns ────
adapter = (_ENGINE / 'adapters/hh/adapter.py').read_text(encoding="utf-8")
check("the loop asks each layer whether it delivered",
      "declared = cover_delivery_of(result.details)" in adapter)
check("the delivery is written onto the result the record is built from",
      "result.details = {**(result.details or {}), **cover_delivery}" in adapter)
check("the delivery is merged last, so an earlier layer's delivery is not "
      "overwritten by a terminal layer that says nothing",
      re.search(r"\{\*\*\(result\.details or \{\}\), \*\*cover_delivery\}", adapter) is not None)

# The chat handler's narrower question — "did an earlier layer already send it" —
# is now that same fact filtered, not a second boolean tracking the same thing.
check("cover_sent_via_modal is derived from the one carried fact",
      "cover_delivery.get('cover_delivered') == 'modal'" in adapter)
check("the old second carrier is gone", "cover_sent_in_modal" not in adapter)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
