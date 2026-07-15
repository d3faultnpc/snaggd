"""snaggd REST API — FastAPI wrapper over the HH apply agent.

Run: uvicorn api:app --host 127.0.0.1 --port 8000
Docs: http://127.0.0.1:8000/api/docs
Auth: X-API-Key header (set API_KEY in .env)
"""

import base64
import dataclasses
import json
import re
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

# Must run before the onboarding.resume_parser import below: ResumeParser.TEXT_MODEL/
# MULTIMODAL_MODEL are class-level attrs read via os.getenv() at import time (session
# 2026-07-15 finding — a real request hit dead hardcoded model slugs because .env's
# LLM_MODEL/RESUME_PARSE_MODEL overrides hadn't loaded yet). get_data_root(), not a bare
# Path(__file__).parent, so this also resolves correctly in a frozen/packaged build.
from app_paths import get_data_root

try:
    from dotenv import load_dotenv
    load_dotenv(get_data_root() / ".env")
except ImportError:
    pass

from onboarding.resume_parser import ResumeData, ResumeParser
from profiles import PROFILES_DIR, ProfileError, resolve_profile

_BASE_DIR = Path(__file__).parent
# Guards PROFILES_DIR / name from path traversal (name comes straight from the
# request body) — deliberately not reusing onboarding/wizard.py here, its
# module-level argparse + input() would hang this long-lived process if imported.
_PROFILE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=True)

app = FastAPI(title="snaggd", version="0.4.1", docs_url="/api/docs")

# ── In-memory session store ───────────────────────────────────────────────────
# {id: {state, thread, stop_event, started_at, result, error}}
# state: starting | running | done | error | stopping
_sessions: dict = {}


# ── Auth ──────────────────────────────────────────────────────────────────────
def _require_key(x_api_key: str = Security(_api_key_scheme)):
    from config import CONFIG
    if not CONFIG.api_key or x_api_key != CONFIG.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ── Request / response models ─────────────────────────────────────────────────
class SessionStartRequest(BaseModel):
    profile: Optional[str] = None
    max_vacancies: Optional[int] = None
    dry_run: bool = False
    debug: bool = False


class ConfigPatchRequest(BaseModel):
    min_score: Optional[int] = None
    max_vacancies: Optional[int] = None
    max_skips: Optional[int] = None


class ResumeParseRequest(BaseModel):
    filename: str
    content_b64: str


class CandidateSaveRequest(BaseModel):
    profile: str
    candidate: dict


# ── Background session worker ─────────────────────────────────────────────────
def _session_worker(session_id: str, req: SessionStartRequest) -> None:
    """Runs HHAdapter.run() in a background thread. Updates _sessions[id] on state changes."""
    from adapters.hh.adapter import HHAdapter
    from logger import Logger

    session = _sessions[session_id]
    session["state"] = "running"
    try:
        try:
            active_profile = resolve_profile(req.profile, exit_on_error=False)
        except ProfileError as e:
            session.update(state="error", error=str(e))
            return
        data_dir = PROFILES_DIR / active_profile

        adapter = HHAdapter(data_dir=data_dir)
        logger = Logger(applied_log_path=data_dir / "applied_log.json")

        if not adapter.verify():
            session.update(state="error", error="Adapter verification failed (cookies or search URLs missing)")
            return

        if not adapter.start(debug=req.debug):
            session.update(state="error", error="Browser failed to start")
            return

        try:
            new_entries = adapter.run(
                logger=logger,
                dry_run=req.dry_run,
                debug=req.debug,
                stop_event=session["stop_event"],
                max_vacancies=req.max_vacancies,
            )
            applied = sum(1 for e in new_entries if e.get("status", "").startswith("applied"))
            skipped = sum(1 for e in new_entries if
                         "skipped" in e.get("status", "") or "blocked" in e.get("status", ""))
            session.update(state="done", result={"applied": applied, "skipped": skipped})
        finally:
            adapter.close()

    except Exception as exc:
        session.update(state="error", error=str(exc))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
def health():
    from config import CONFIG
    return {"status": "ok", "version": "0.4.1", "headless": CONFIG.headless}


@app.post("/api/v1/session/start", dependencies=[Depends(_require_key)])
def session_start(req: SessionStartRequest):
    session_id = str(uuid.uuid4())[:8]
    stop_event = threading.Event()
    _sessions[session_id] = {
        "state": "starting",
        "profile": req.profile,
        "stop_event": stop_event,
        "started_at": datetime.now().isoformat(),
        "result": None,
        "error": None,
    }
    t = threading.Thread(target=_session_worker, args=(session_id, req), daemon=True)
    _sessions[session_id]["thread"] = t
    t.start()
    return {"id": session_id, "state": "starting", "profile": req.profile}


@app.get("/api/v1/session/{session_id}/status", dependencies=[Depends(_require_key)])
def session_status(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    s = _sessions[session_id]
    return {
        "id": session_id,
        "state": s["state"],
        "profile": s.get("profile"),
        "started_at": s["started_at"],
        "result": s["result"],
        "error": s["error"],
    }


@app.post("/api/v1/session/{session_id}/stop", dependencies=[Depends(_require_key)])
def session_stop(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    _sessions[session_id]["stop_event"].set()
    _sessions[session_id]["state"] = "stopping"
    return {"id": session_id, "state": "stopping"}


@app.get("/api/v1/log", dependencies=[Depends(_require_key)])
def log_list(limit: int = 50, offset: int = 0, profile: Optional[str] = None):
    from logger import Logger
    try:
        active_profile = resolve_profile(profile, exit_on_error=False)
    except ProfileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log_path = PROFILES_DIR / active_profile / "applied_log.json"
    all_entries = Logger(applied_log_path=log_path).load_applied_log()
    # session_end entries are meta-records, exclude from vacancy log
    vacancy_entries = [e for e in all_entries if e.get("type") != "session_end"]
    return {
        "total": len(vacancy_entries),
        "offset": offset,
        "limit": limit,
        "profile": active_profile,
        "entries": vacancy_entries[offset: offset + limit],
    }


@app.get("/api/v1/log/{vacancy_id}", dependencies=[Depends(_require_key)])
def log_detail(vacancy_id: str, profile: Optional[str] = None):
    from logger import Logger, _extract_vacancy_id
    try:
        active_profile = resolve_profile(profile, exit_on_error=False)
    except ProfileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log_path = PROFILES_DIR / active_profile / "applied_log.json"
    for entry in Logger(applied_log_path=log_path).load_applied_log():
        eid = entry.get("vacancy_id") or _extract_vacancy_id(entry.get("url", ""))
        if eid == vacancy_id:
            return entry
    raise HTTPException(status_code=404, detail="Vacancy not found in log")


@app.get("/api/v1/config", dependencies=[Depends(_require_key)])
def config_read():
    from config import CONFIG
    return {
        "min_score": CONFIG.min_score,
        "max_vacancies": CONFIG.max_vacancies_per_session,
        "max_skips": CONFIG.max_skips,
        "headless": CONFIG.headless,
        "fill_tests": CONFIG.fill_tests,
    }


@app.patch("/api/v1/config", dependencies=[Depends(_require_key)])
def config_patch(req: ConfigPatchRequest):
    from config import CONFIG
    if req.min_score is not None:
        CONFIG.min_score = req.min_score
    if req.max_vacancies is not None:
        CONFIG.max_vacancies_per_session = req.max_vacancies
    if req.max_skips is not None:
        CONFIG.max_skips = req.max_skips
    return {
        "updated": True,
        "min_score": CONFIG.min_score,
        "max_vacancies": CONFIG.max_vacancies_per_session,
        "max_skips": CONFIG.max_skips,
    }


# ── Profile endpoints ─────────────────────────────────────────────────────────

def _profile_info(name: str, data_dir: Path) -> dict:
    """Build profile summary dict from profile directory."""
    info: dict = {"name": name, "data_dir": str(data_dir), "configured": False}
    candidate = data_dir / "candidate.md"
    if candidate.exists():
        info["configured"] = True
        first_line = candidate.read_text(encoding="utf-8").splitlines()[0] if candidate.stat().st_size else ""
        info["candidate_headline"] = first_line.lstrip("#").strip()
    log_path = data_dir / "applied_log.json"
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text(encoding="utf-8"))
            vacancy_entries = [e for e in entries if e.get("type") != "session_end"]
            info["total_processed"] = len(vacancy_entries)
            info["total_applied"] = sum(
                1 for e in vacancy_entries if str(e.get("status", "")).startswith("applied"))
            last = next((e for e in reversed(entries) if e.get("type") == "session_end"), None)
            info["last_session"] = last.get("date") if last else None
        except Exception:
            pass
    return info


@app.get("/api/v1/profiles", dependencies=[Depends(_require_key)])
def profiles_list():
    if not PROFILES_DIR.exists():
        return {"profiles": []}
    result = []
    for p in sorted(PROFILES_DIR.iterdir()):
        if p.is_dir():
            result.append(_profile_info(p.name, p))
    return {"profiles": result}


@app.get("/api/v1/profiles/{name}", dependencies=[Depends(_require_key)])
def profile_detail(name: str):
    data_dir = PROFILES_DIR / name
    if not data_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    return _profile_info(name, data_dir)


# ── Onboarding endpoints (GUI wizard) ───────────────────────────────────────
# Deliberately reuse only ResumeParser/ResumeData (pure, no side effects at
# import time) — never onboarding/wizard.py itself, see _PROFILE_NAME_RE comment.

@app.post("/api/v1/onboarding/parse", dependencies=[Depends(_require_key)])
def onboarding_parse(req: ResumeParseRequest):
    import os

    suffix = Path(req.filename).suffix.lower()
    if suffix not in ResumeParser.SUPPORTED_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix or '(none)'}")

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="LLM_API_KEY not set on the server — cannot parse resumes")

    try:
        raw = base64.b64decode(req.content_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="content_b64 is not valid base64")

    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        data = ResumeParser(client).parse_file(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Parse error: {e}")
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    return dataclasses.asdict(data)


@app.post("/api/v1/onboarding/save", dependencies=[Depends(_require_key)])
def onboarding_save(req: CandidateSaveRequest):
    # fullmatch, not match — match("abc\n") passes because `$` accepts a trailing
    # newline, which would silently become part of a directory name otherwise.
    if not _PROFILE_NAME_RE.fullmatch(req.profile):
        raise HTTPException(status_code=400, detail="profile must be alphanumeric (dash/underscore allowed)")

    data_dir = PROFILES_DIR / req.profile
    md_out = data_dir / "candidate.md"
    existing_md = md_out.read_text(encoding="utf-8") if md_out.exists() else ""

    # Validate + render BEFORE touching the filesystem. ResumeData(**candidate) only
    # checks key names (dataclasses do no runtime type checking), so a well-formed-looking
    # payload with e.g. identity as a string instead of a dict passes construction and
    # only blows up (AttributeError) inside to_md() — catch that here too, not just
    # TypeError, so a malformed payload 400s cleanly instead of 500ing after already
    # having read (or worse, written) into data_dir.
    try:
        data = ResumeData(**req.candidate)
        rendered_md = ResumeParser(None).to_md(data, existing_content=existing_md)
    except (TypeError, AttributeError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"candidate payload doesn't match schema: {e}")

    data_dir.mkdir(parents=True, exist_ok=True)
    md_out.write_text(rendered_md, encoding="utf-8")

    json_out = data_dir / "candidate.json"
    payload = dataclasses.asdict(data)
    payload.pop("suggested_queries", None)  # parser convenience field, not part of the schema
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"saved": True, "profile": req.profile}


if __name__ == "__main__":
    # Lets this file run directly (`python api.py` / a PyInstaller-frozen build of
    # it) in addition to the `uvicorn api:app` dev invocation in the module
    # docstring — a frozen build has no external `uvicorn` CLI to shell out to.
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
