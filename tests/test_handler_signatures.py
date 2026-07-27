"""
Regression coverage for session 56's handler-contract refactor: cover_letter
removed as a positional process() argument everywhere (replaced by on-demand
llm_cover.cover(vacancy_text, vacancy_id) calls inside whichever handler
actually needs one). Verifies every handler still constructs, still
dispatches via FormHandlers, and that process() no longer requires a
cover_letter argument at all — a stale caller passing one positionally would
now raise, which is exactly the regression this guards against.
"""
import sys
import inspect
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        from adapters.hh.handlers import FormHandlers
        from adapters.hh.handlers.base import BaseHandler

results = []


def check(label, condition):
    status = "✅" if condition else "❌"
    print(f"  {status} {label}")
    results.append(bool(condition))


handlers = FormHandlers(data_dir=Path("/tmp/does-not-need-to-exist")).handlers
check("FormHandlers constructs all 6 handlers", len(handlers) == 6)

for h in handlers:
    name = type(h).__name__
    sig = inspect.signature(h.process)
    params = list(sig.parameters.values())
    check(f"{name}.process() has no cover_letter positional param",
          not any(p.name == "cover_letter" for p in params))
    check(f"{name}.process() accepts **kwargs",
          any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params))
    check(f"{name} is a real BaseHandler subclass", isinstance(h, BaseHandler))

# BaseHandler's own abstract contract matches (base.py itself was edited)
base_sig = inspect.signature(BaseHandler.process)
check("BaseHandler.process() ABC no longer declares cover_letter",
      not any(p.name == "cover_letter" for p in base_sig.parameters.values()))

# CLI and API entrypoints still import cleanly post-refactor (catches any
# leftover reference to the old llm_cover.generate()/cover_letter contract).
# main.py resolves --profile at module level (pre-existing design, #31's
# profile resolution law) — needs a real --profile arg, a bare import isn't
# representative of how the CLI is actually invoked.
try:
    from profiles import list_profiles as _list_profiles
    _real_profile = _list_profiles()[0]
    _saved_argv = sys.argv
    sys.argv = ["main.py", "--profile", _real_profile, "--dry-run"]
    import main  # noqa: F401
    check("main.py (CLI entrypoint) imports cleanly with a real --profile", True)
except Exception as e:
    check(f"main.py (CLI entrypoint) imports cleanly with a real --profile — {e}", False)
finally:
    sys.argv = _saved_argv

try:
    import api  # noqa: F401
    check("api.py (app/sidecar entrypoint) imports cleanly", True)
except Exception as e:
    check(f"api.py (app/sidecar entrypoint) imports cleanly — {e}", False)

print()
total = len(results)
passed = sum(results)
print(f"{passed}/{total} passed")
if passed != total:
    sys.exit(1)
