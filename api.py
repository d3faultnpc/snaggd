"""snaggd REST API — FastAPI wrapper over the HH apply agent.

Run: uvicorn api:app --host 127.0.0.1 --port 8000
Docs: http://127.0.0.1:8000/api/docs
Auth: X-API-Key header (set API_KEY in .env)
"""

import threading
import uuid
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI(title="snaggd", version="0.3.1", docs_url="/api/docs")

# ── In-memory session store ───────────────────────────────────────────────────
# {id: {state, thread, stop_event, started_at, result, error}}
# state: starting | running | done | error | stopping
_sessions: dict = {}


# ── Auth ──────────────────────────────────────────────────────────────────────
def _require_key(x_api_key: str = Header(...)):
    from config import CONFIG
    if not CONFIG.api_key or x_api_key != CONFIG.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ── Request / response models ─────────────────────────────────────────────────
class SessionStartRequest(BaseModel):
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
        adapter = HHAdapter()
        logger = Logger()

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
        "stop_event": stop_event,
        "started_at": datetime.now().isoformat(),
        "result": None,
        "error": None,
    }
    t = threading.Thread(target=_session_worker, args=(session_id, req), daemon=True)
    _sessions[session_id]["thread"] = t
    t.start()
    return {"id": session_id, "state": "starting"}


@app.get("/api/v1/session/{session_id}/status", dependencies=[Depends(_require_key)])
def session_status(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    s = _sessions[session_id]
    return {
        "id": session_id,
        "state": s["state"],
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
