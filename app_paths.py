"""Frozen-aware resolution of where persistent app data lives.

Dev (running from source): repo root, identical to the old `Path(__file__).parent` behavior
in config.py/profiles.py — zero change to the CLI workflow.

Frozen (packaged app, `sys.frozen`): a real per-user OS data directory, never inside the app
bundle itself. Writing inside the bundle fails outright on a standard `/Applications`-style
install (not user-writable), breaks again after code-signing (integrity checks), and doesn't
survive app updates even where it happens to be writable — confirmed via the #24 installer
spike, 2026-07-13.

Deliberately has no other project imports: profiles.py must be importable before config.py
(profiles.py sets DATA_DIR in os.environ before Config's dataclass fields are evaluated), so
anything both of them share has to sit below both, not inside either.
"""
import os
import sys
from pathlib import Path

_APP_NAME = "snaggd"


def get_data_root() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).parent

    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / _APP_NAME
    elif sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home())) / _APP_NAME
    else:
        root = Path.home() / ".local" / "share" / _APP_NAME

    root.mkdir(parents=True, exist_ok=True)
    return root
