"""Profile discovery and resolution — the one rule for picking a data directory.

Shared by main.py, onboarding/wizard.py, and api.py so the CLI and the future app
backend resolve profiles identically — no separate "legacy" behavior for either.

Deliberately import-light (no config.py dependency): main.py/wizard.py must call
resolve_profile() BEFORE importing config, since DATA_DIR has to already be in
os.environ by the time Config's dataclass fields are evaluated.
"""
import sys
from pathlib import Path
from typing import NoReturn, Optional

BASE_DIR = Path(__file__).parent
PROFILES_DIR = BASE_DIR / "data" / "profiles"


class ProfileError(ValueError):
    pass


def list_profiles() -> list[str]:
    """Names of configured profiles — a dir counts once it has candidate.md or candidate.json."""
    if not PROFILES_DIR.exists():
        return []
    return sorted(
        p.name for p in PROFILES_DIR.iterdir()
        if p.is_dir() and ((p / "candidate.md").exists() or (p / "candidate.json").exists())
    )


def resolve_profile(requested: Optional[str], *, exit_on_error: bool = True) -> str:
    """The one rule for picking which profile a run applies to.

    - requested given           -> must exist, else error listing what's available
    - nothing given, 0 profiles -> error: no profiles yet, run the wizard
    - nothing given, 1 profile  -> auto-select it (announced, not silent)
    - nothing given, 2+ profiles -> error: ambiguous, name one explicitly

    There is no fallback to a flat/legacy data dir in any branch — a profile is
    always required, whether the caller has one resume or several.

    exit_on_error=True  (CLI, single-shot process): prints and sys.exit(1)
    exit_on_error=False (API, long-lived process):  raises ProfileError instead
    """
    profiles = list_profiles()

    if requested:
        if requested not in profiles:
            available = ", ".join(profiles) if profiles else "(none)"
            return _fail(f"Profile '{requested}' not found. Available: {available}", exit_on_error)
        return requested

    if not profiles:
        return _fail("No profiles found. Run: python onboarding/wizard.py --profile <name>", exit_on_error)

    if len(profiles) == 1:
        print(f"👤 Profile: {profiles[0]} (only one found, auto-selected)")
        return profiles[0]

    return _fail(
        f"Multiple profiles found ({', '.join(profiles)}) — pass --profile <name> to choose one.",
        exit_on_error,
    )


def _fail(message: str, exit_on_error: bool) -> NoReturn:
    if exit_on_error:
        print(f"❌ {message}")
        sys.exit(1)
    raise ProfileError(message)
