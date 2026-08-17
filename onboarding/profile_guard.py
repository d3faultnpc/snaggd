"""Guards against a profile save that destroys the profile.

Why this exists, concretely (2026-08-11):

A save from the GUI wizard replaced a populated candidate.md — full work
history, cases, skills — with an empty placeholder skeleton, and created an empty
candidate.json alongside it. Every vacancy scored after that was scored against
nothing; the run reported ordinary-looking numbers (0, 30, 50) and there was no
sign anywhere that the profile had been emptied. It was found a day later by
comparing file sizes.

Nothing in the save path was wrong by its own contract. `onboarding_save`
validated the SHAPE of the payload and wrote it. A wizard opened blank produces
a shape-valid payload full of empty lists, and shape validation has no opinion
about that. The missing rule is not about types:

    a save may add to a profile, or change it — it may not empty it.

The second half matters because candidate.md is *rendered from* candidate.json
by every wizard step (onboarding/wizard.py::_write_candidate). An emptied
candidate.json is therefore not an inert file — it is the next wipe of
candidate.md, waiting for the user to touch any wizard step at all.
"""

import re
from pathlib import Path

# The fields that carry a profile's actual substance. Identity, salary and
# search rules are deliberately NOT here: a profile legitimately has those empty,
# and a wizard step that only sets a region must stay allowed to save.
SUBSTANCE_FIELDS = ("cases", "skills", "tools", "languages")

# Placeholders `ResumeParser.to_md()` USED to write in place of content it did
# not have. It no longer writes any of them (2026-08-14 — they were going into
# the system prompt as if they were candidate facts), but files written before
# that are still on disk, so a skeleton has to stay recognisable by them. Only
# the ones that can appear mid-line matter here; the rest begin with '#' and are
# skipped as comments/headings by the scan below.
_SKELETON_MARKERS = ("# EMPTY — ", "MISSING — add", "MISSING — ", "# SKIPPABLE", "# HINT:")
# `name:` with nothing after it — a named slot, filled with nothing.
_EMPTY_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:\s*$")


def substance_of(candidate: dict) -> int:
    """How many real items a candidate payload carries."""
    if not isinstance(candidate, dict):
        return 0
    total = 0
    for field in SUBSTANCE_FIELDS:
        value = candidate.get(field)
        if isinstance(value, list):
            total += len(value)
    return total


def md_looks_like_skeleton(markdown: str) -> bool:
    """True if this candidate.md is a placeholder rather than a filled profile.

    Used for profiles that predate candidate.json — one such profile existed, which is
    exactly why its wipe went unnoticed: with no candidate.json on disk there
    was no structured 'before' to compare against, so a substance check that
    only read the JSON would have found nothing to protect.

    Asks whether the file carries any substance at all, rather than whether it
    contains a known placeholder string. The marker-matching version could only
    recognise skeletons the renderer of the day happened to produce; since that
    renderer now writes nothing at all for an absent value, an emptied profile
    is headings and blank lines, and matches no marker. A content predicate
    survives the next change to the renderer too.
    """
    if not markdown or not markdown.strip():
        return True
    for raw in markdown.splitlines():
        line = raw.strip()
        # Headings, comments and the legacy managed-block markers are structure,
        # not content: a file that is only structure is a skeleton.
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        # A key with nothing after it is structure too — it names a slot and
        # fills none of it, exactly like a heading. Without this, a blank profile
        # written as its own keys with no values reads as substance, and is then
        # allowed to overwrite a real one — the wipe this predicate exists to
        # stop, arriving in the shape of an empty form rather than an empty file.
        if _EMPTY_KEY_RE.match(line):
            continue
        if any(marker in line for marker in _SKELETON_MARKERS):
            continue
        return False
    return True


def existing_substance(data_dir: Path) -> int:
    """Substance already on disk for this profile — the MAX across both files.

    Both are consulted and the larger wins; JSON is not allowed to speak for the
    profile on its own. That is not defensive programming, it is the exact state
    that profile was left in after the 2026-08-11 wipe: an empty candidate.json sitting
    next to a restored, fully populated candidate.md. A JSON-first reading calls
    that profile empty and waves the next wipe straight through — the guard would
    have been blind to the very situation it was written for.

    Returns 0 for a profile that genuinely has nothing yet. That one must stay
    overwritable, or first-run onboarding could never save anything.
    """
    import json

    from_json = 0
    json_path = Path(data_dir) / "candidate.json"
    if json_path.exists():
        try:
            from_json = substance_of(json.loads(json_path.read_text(encoding="utf-8")))
        except Exception:
            pass  # unreadable JSON — the markdown still gets its say below

    from_md = 0
    md_path = Path(data_dir) / "candidate.md"
    if md_path.exists():
        try:
            if not md_looks_like_skeleton(md_path.read_text(encoding="utf-8")):
                # Real content, unknown amount. 1 is enough: the guard only ever
                # asks "is there something here that an empty save would destroy".
                from_md = 1
        except Exception:
            pass

    return max(from_json, from_md)


def check_destructive_save(data_dir: Path, candidate: dict):
    """Returns None if this save is safe, or a human-readable reason if it wipes.

    Deliberately narrow: it fires only when the incoming payload has NO substance
    at all while the profile on disk has some. A save that reduces a profile from
    ten skills to three is a legitimate edit and is none of this function's
    business. A save that reduces it to zero is what happened on 2026-08-11, and
    is never what a user means.
    """
    incoming = substance_of(candidate)
    if incoming > 0:
        return None
    existing = existing_substance(data_dir)
    if existing == 0:
        return None
    return (
        "This save would empty the profile: it carries no work history, skills, "
        "tools or languages, and the profile on disk has them. This is what an "
        "unfilled wizard form looks like. Nothing was written. Re-open the "
        "profile so the wizard loads the existing data, or pass overwrite=true "
        "if erasing it is genuinely what you want."
    )


BACKUP_DIRNAME = ".backups"
BACKUP_KEEP = 10


def backup_profile(data_dir: Path, filenames=("candidate.md", "candidate.json")) -> str:
    """Copy the current profile files aside before a save. Returns the stamp.

    Backups go into a `.backups/` subdirectory, not next to the originals.
    The old arrangement dropped `candidate.md.<timestamp>.bak` into the
    profile directory itself, where it was invisible to the user, unlisted by any
    UI, and only found because someone ran `ls -la` while hunting a bug a day
    later. A backup nobody can find is a backup that does not exist.

    One stamp per save across all files, so a save's backup is recoverable as a
    matched pair rather than two independently-timed halves. Older sets beyond
    BACKUP_KEEP are pruned — this used to grow without limit.
    """
    import shutil
    from datetime import datetime

    data_dir = Path(data_dir)
    backup_dir = data_dir / BACKUP_DIRNAME
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")

    copied = False
    for name in filenames:
        src = data_dir / name
        if src.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup_dir / f"{stamp}.{name}")
            copied = True
    if not copied:
        return stamp

    stamps = sorted({p.name.split(".", 1)[0] for p in backup_dir.glob("*.candidate.*")})
    for old in stamps[:-BACKUP_KEEP]:
        for stale in backup_dir.glob(f"{old}.*"):
            try:
                stale.unlink()
            except OSError:
                pass
    return stamp


def list_backups(data_dir: Path) -> list:
    """Backup sets for this profile, newest first: [{stamp, files, substance}].

    `substance` is what each set would restore, so a UI (or a person reading the
    API) can tell a real profile from a skeleton without opening the files.
    """
    import json

    backup_dir = Path(data_dir) / BACKUP_DIRNAME
    if not backup_dir.is_dir():
        return []
    sets: dict = {}
    for path in backup_dir.glob("*.candidate.*"):
        stamp, _, filename = path.name.partition(".")
        entry = sets.setdefault(stamp, {"stamp": stamp, "files": [], "substance": None})
        entry["files"].append(filename)
        if filename == "candidate.json":
            try:
                entry["substance"] = substance_of(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
    return sorted(sets.values(), key=lambda e: e["stamp"], reverse=True)


def needs_migration(data_dir: Path) -> bool:
    """A pre-candidate.json profile: real markdown, no structured data yet.

    The GUI wizard prefills itself from candidate.json. For a profile like this
    it gets nothing, opens blank, and the first save writes that blank over a
    real profile. Callers should surface this and offer the migration
    (scripts/migrate_candidate.py, which by design never writes to a live
    profile) instead of presenting an empty form.
    """
    data_dir = Path(data_dir)
    if (data_dir / "candidate.json").exists():
        return False
    md_path = data_dir / "candidate.md"
    if not md_path.exists():
        return False
    try:
        return not md_looks_like_skeleton(md_path.read_text(encoding="utf-8"))
    except Exception:
        return False
