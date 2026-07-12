"""snaggd REST API — FastAPI wrapper over the HH apply agent.

Run: uvicorn api:app --host 127.0.0.1 --port 8000
Docs: http://127.0.0.1:8000/api/docs
Auth: X-API-Key header (set API_KEY in .env)
"""

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from profiles import PROFILES_DIR, ProfileError, resolve_profile

_BASE_DIR = Path(__file__).parent

_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=True)

app = FastAPI(title="snaggd", version="0.3.1", docs_url="/api/docs")

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
    return {"status": "ok", "version": "0.3.1", "headless": CONFIG.headless}


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
def log_list(limit: int = 50, offset: int = 0):
    from logger import Logger
    all_entries = Logger().load_applied_log()
    # session_end entries are meta-records, exclude from vacancy log
    vacancy_entries = [e for e in all_entries if e.get("type") != "session_end"]
    return {
        "total": len(vacancy_entries),
        "offset": offset,
        "limit": limit,
        "entries": vacancy_entries[offset: offset + limit],
    }


@app.get("/api/v1/log/{vacancy_id}", dependencies=[Depends(_require_key)])
def log_detail(vacancy_id: str):
    from logger import Logger, _extract_vacancy_id
    for entry in Logger().load_applied_log():
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
