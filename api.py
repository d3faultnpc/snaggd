"""snaggd REST API — FastAPI wrapper over the HH apply agent.

Run: uvicorn api:app --host 127.0.0.1 --port 8000
Docs: http://127.0.0.1:8000/api/docs
Auth: X-API-Key header (set API_KEY in .env)
"""

import base64
import dataclasses
import json
import os
import re
import tempfile
import threading
import time
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
from utils.events import EventReporter
from utils.filters import load_stop_filters, patch_filters_json

_BASE_DIR = Path(__file__).parent
# Guards PROFILES_DIR / name from path traversal (name comes straight from the
# request body) — deliberately not reusing onboarding/wizard.py here, its
# module-level argparse + input() would hang this long-lived process if imported.
_PROFILE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=True)

app = FastAPI(title="snaggd", version="0.4.1", docs_url="/api/docs")

# ── In-memory session store ───────────────────────────────────────────────────
# {id: {state, thread, stop_event, started_at, result, error, reporter}}
# state: starting | running | done | error | stopping
_sessions: dict = {}

# ── In-memory connector-login store ───────────────────────────────────────────
# {name: {state, error, ...extra}} — one entry per Playwright-based adapter that
# needs human-in-the-loop cookie capture (hh now; workday/rabota.ru later, see
# adapters/00_adapter_strategy.md). API-based adapters (Greenhouse, Lever) never
# register here — no login step exists for them at all.
# state: idle | starting | running | done | error
_connector_logins: dict = {}


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
    # Dev-build-only single-vacancy override — mirrors main.py's --url flag
    # (forces debug=True, max_vacancies=1, overrides the search-URL list with
    # just this one). Gated in the app UI behind is_dev_build(), not enforced
    # server-side: a crafted request could set it regardless, but there's no
    # real exposure — it only narrows a run to one URL, same privilege as an
    # ordinary run already has.
    target_url: Optional[str] = None


class ConfigPatchRequest(BaseModel):
    min_score: Optional[int] = None
    max_vacancies: Optional[int] = None
    max_skips: Optional[int] = None


class ResumeParseRequest(BaseModel):
    filename: str
    content_b64: str


class MinMatchPatchRequest(BaseModel):
    min_match: int


class CandidateSaveRequest(BaseModel):
    profile: str
    candidate: dict


# ── Session-start preflight ──────────────────────────────────────────────────
def _check_hh_live() -> bool:
    """Headless liveness probe — separate from /api/v1/connectors/hh/status,
    which only inspects the cookie's own stated expiry locally (no network
    call at all) and can't see server-side invalidation. This actually loads
    the saved cookies into a throwaway headless context and hits an
    auth-required page, so it answers "will a real run actually work" rather
    than "hasn't the file's own timestamp lapsed yet". Always headless
    regardless of the run's configured browser mode — this is a quick check,
    not the real work."""
    from playwright.sync_api import sync_playwright
    from config import CONFIG

    if not CONFIG.cookies_path.exists():
        return False
    try:
        cookies = json.loads(CONFIG.cookies_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    try:
        with sync_playwright() as p:
            # Confirmed live 2026-08-02: HH.ru detects navigator.webdriver=true and
            # headless Chrome's own UA string, and withholds the resume card in
            # response even with genuinely valid cookies — verified by patching both
            # and immediately seeing the resume link, at the 1s mark, with the exact
            # same cookies that showed nothing after 12s unpatched.
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.goto("https://hh.ru/applicant/profile/me", wait_until="domcontentloaded", timeout=30_000)
            # A resume link is a real positive signal — only present when
            # actually authenticated. Wait for it directly (up to 8s) rather
            # than a fixed short sleep + count check, which false-negatived
            # on a genuinely fresh session whose resume card hadn't finished
            # rendering yet (found live 2026-08-01). The old "still on
            # /applicant/*" fallback is gone too — it was trivially true even
            # logged out, since HH doesn't hard-redirect this path for
            # anonymous visitors, so it always reported "live" regardless of
            # real cookie state.
            try:
                page.wait_for_selector("a[href*='/resume/']", timeout=8_000)
                has_resume_link = True
            except Exception:
                has_resume_link = False
            browser.close()
            return has_resume_link
    except Exception:
        return False


# ── Background session worker ─────────────────────────────────────────────────
def _session_worker(session_id: str, req: SessionStartRequest) -> None:
    """Runs HHAdapter.run() in a background thread. Updates _sessions[id] on state changes."""
    from adapters.hh.adapter import HHAdapter
    from core.llm_agent import set_session_reporter
    from logger import Logger

    session = _sessions[session_id]
    # Worker-side lifecycle narration goes through the same per-session
    # reporter the adapter mirrors its prints into — an attached GUI client then
    # sees one continuous stream: preflight (here) → vacancy loop (adapter)
    # → wrap-up (here). Errors emit before session.update() so the poll
    # that observes the state flip already finds the explaining line.
    reporter = session["reporter"]
    session["state"] = "running"
    try:
        try:
            active_profile = resolve_profile(req.profile, exit_on_error=False)
        except ProfileError as e:
            reporter.emit(f"Session failed: {e}", level="error")
            session.update(state="error", error=str(e))
            return
        # Resolved name, not the possibly-None req.profile — /pause and
        # /resume only see `session`, not this function's locals, and need
        # the real profile to read applied_log.json for a mid-run flush.
        session["profile"] = active_profile
        data_dir = PROFILES_DIR / active_profile
        reporter.emit(
            f"Session starting — profile: {active_profile}"
            + (" · DRY RUN" if req.dry_run else "")
        )

        # Mirrors _chat_completion()'s call-counter into this session's own
        # reporter (session 56) — was print-only before, invisible once the
        # app runs from a bundled .app with no attached console.
        set_session_reporter(reporter)

        adapter = HHAdapter(data_dir=data_dir, reporter=reporter)
        logger = Logger(applied_log_path=data_dir / "applied_log.json")

        # Single-URL override (session 55): threaded through as an explicit
        # adapter.run(target_url=...) argument, not a monkeypatch — a prior
        # version patched adapter.browser._load_search_urls at this point,
        # which was implicated in a real live-run incident (override
        # silently not taking effect, normal search vacancies got real
        # applications submitted instead). get_vacancies()/get_vacancy_urls()
        # now take target_url directly, plus run() hard-stops if what comes
        # back doesn't match — see adapters/hh/adapter.py and browser.py.
        if req.target_url:
            reporter.emit(f"Single-URL debug mode: {req.target_url}")
            req.debug = True

        if not adapter.verify():
            reporter.emit("Adapter verification failed (cookies or search URLs missing)", level="error")
            session.update(state="error", error="Adapter verification failed (cookies or search URLs missing)")
            return

        # Preflight — cheap headless HH-liveness check before committing to
        # the real (possibly visible/corner) browser mode the user configured.
        session["state"] = "checking"

        effective_max = req.max_vacancies
        if req.target_url:
            effective_max = 1

        reporter.emit("Preflight: checking connection with HH…")
        if not _check_hh_live():
            reporter.emit("HH session appears expired — run login.py again to refresh cookies", level="error")
            session.update(state="error", error="HH session appears expired — run login.py again to refresh cookies")
            return

        # A stop requested anywhere during the liveness check was silently
        # dropped until now: nothing checked stop_event between 'checking'
        # and the real browser opening, so it opened and the vacancy loop
        # ran regardless (found via live testing, session 54). stop_event
        # alone is enough here — pause_event needs no equivalent check:
        # pausing before the browser exists doesn't skip opening it, it just
        # means the loop holds before vacancy #1 the instant it starts, which
        # adapter.py's own per-iteration check already does.
        if session["stop_event"].is_set():
            reporter.emit("Stopped during preflight — browser never opened")
            session.update(state="done", result={"applied": 0, "skipped": 0, "dry_run": 0})
            return

        session["state"] = "running"

        reporter.emit("Starting browser…")
        if not adapter.start(debug=req.debug):
            reporter.emit("Browser failed to start", level="error")
            session.update(state="error", error="Browser failed to start")
            return

        try:
            new_entries = adapter.run(
                logger=logger,
                dry_run=req.dry_run,
                debug=req.debug,
                stop_event=session["stop_event"],
                pause_event=session["pause_event"],
                max_vacancies=effective_max,
                target_url=req.target_url,
            )
            applied = sum(1 for e in new_entries if e.get("status", "").startswith("applied"))
            skipped = sum(1 for e in new_entries if
                         "skipped" in e.get("status", "") or "blocked" in e.get("status", ""))
            # dry_run-status entries fall into neither bucket above — without this a
            # fully-successful dry run (real scoring, no submission by design) always
            # reported 0/0, indistinguishable from a run that scored nothing at all.
            dry_run_count = sum(1 for e in new_entries if e.get("status") == "dry_run")
            # Emit BEFORE flipping state: the frontend stops polling once it
            # observes done/error, and its events fetch rides the same tick —
            # emitting after the flip would race that final fetch.
            reporter.emit(
                f"Done — {applied} applied · {skipped} skipped"
                + (f" · {dry_run_count} dry-run" if dry_run_count else "")
            )
            session.update(state="done", result={"applied": applied, "skipped": skipped, "dry_run": dry_run_count})
        finally:
            adapter.close()

    except Exception as exc:
        reporter.emit(f"Session crashed: {exc}", level="error")
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
    pause_event = threading.Event()
    _sessions[session_id] = {
        "state": "starting",
        "profile": req.profile,
        "stop_event": stop_event,
        "pause_event": pause_event,
        "started_at": datetime.now().isoformat(),
        "result": None,
        "error": None,
        # Live narration feed for an attached GUI client (utils/events.py) — the
        # worker and adapter write into it, /session/{id}/events reads it.
        "reporter": EventReporter(),
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
        "paused": s["pause_event"].is_set(),
    }


@app.post("/api/v1/session/{session_id}/stop", dependencies=[Depends(_require_key)])
def session_stop(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    # A paused session is blocked inside adapter.run()'s wait loop, which
    # only re-checks stop_event while pause_event is set (see adapter.py) —
    # clearing pause_event here lets that wait loop notice the stop instead
    # of blocking forever waiting for a /resume that will never come.
    # Request-received narration, distinct from the adapter's own
    # "Stop requested via API" line — that one only appears when the vacancy
    # loop reaches its next checkpoint, which can be a full vacancy later.
    _sessions[session_id]["reporter"].emit("⏹ Stop requested — the current vacancy will finish first")
    _sessions[session_id]["pause_event"].clear()
    _sessions[session_id]["stop_event"].set()
    _sessions[session_id]["state"] = "stopping"
    return {"id": session_id, "state": "stopping"}


@app.post("/api/v1/session/{session_id}/pause", dependencies=[Depends(_require_key)])
def session_pause(session_id: str):
    # print, not just a comment: this line showing up in the sidecar's own
    # stdout is the ground truth for "did the request even reach the Python
    # process" — session 54 found zero "Paused before #N" lines in a whole
    # day's daily log across 7 real test sessions, so the request arriving
    # here at all is exactly the fact in question, not an assumption.
    print(f"⏸ [api] /pause called for session {session_id}")
    if session_id not in _sessions:
        print(f"⏸ [api] session {session_id} not found in _sessions (known: {list(_sessions.keys())})")
        raise HTTPException(status_code=404, detail="Session not found")
    s = _sessions[session_id]
    print(f"⏸ [api] session {session_id} state={s['state']!r} pause_event.is_set()={s['pause_event'].is_set()}")
    if s["state"] not in ("running", "checking"):
        raise HTTPException(status_code=409, detail=f"Cannot pause from state '{s['state']}'")
    s["pause_event"].set()
    s["reporter"].emit("⏸ Pause requested — takes effect after the current vacancy")
    print(f"⏸ [api] pause_event.set() done, is_set()={s['pause_event'].is_set()}")
    return {"id": session_id, "state": s["state"], "paused": True}


@app.post("/api/v1/session/{session_id}/resume", dependencies=[Depends(_require_key)])
def session_resume(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    s = _sessions[session_id]
    if not s["pause_event"].is_set():
        raise HTTPException(status_code=409, detail="Session is not paused")

    s["pause_event"].clear()
    s["reporter"].emit("▶ Resume requested")
    return {"id": session_id, "state": s["state"], "paused": False}


@app.get("/api/v1/session/{session_id}/events", dependencies=[Depends(_require_key)])
def session_events(session_id: str, after: int = 0):
    """Incremental live-narration feed for the Terminal screen.

    `after` is the last seq the client has seen (0 = from the start of what
    the rolling buffer still holds). Polled every 2s alongside /status —
    same deliberate polling-over-push stance as app-bootstrap.js's log
    refresh: the apply loop paces itself 7-25s per vacancy, push buys nothing.
    """
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    events, last_seq = _sessions[session_id]["reporter"].since(after)
    return {"id": session_id, "last_seq": last_seq, "events": events}


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


# ── Connector endpoints ───────────────────────────────────────────────────────
# Generic /connectors/{name}/... surface instead of one-off /hh-* routes, so
# adding workday/rabota.ru later means one new registry entry per dict below,
# not a duplicated endpoint set. See _connector_logins declaration up top.

def _hh_connector_status() -> dict:
    """Real live/expired check, not a hardcoded UI string. hhtoken is the one
    HH.ru cookie with a genuine long-lived numeric expiry (most auth-adjacent
    cookies — hhrole, crypted_hhuid — carry expires=-1, telling us nothing
    about staleness). No live network ping to HH.ru — just whether the token
    we already have has passed its own stated expiry."""
    from config import CONFIG
    if not CONFIG.cookies_path.exists():
        return {"status": "not_connected"}
    try:
        cookies = json.loads(CONFIG.cookies_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "not_connected"}
    token = next(
        (c for c in cookies if c.get("name") == "hhtoken" and str(c.get("domain", "")).endswith("hh.ru")),
        None,
    )
    if not token or not isinstance(token.get("expires"), (int, float)) or token["expires"] < time.time():
        return {"status": "expired"}
    return {"status": "live"}


_CONNECTOR_STATUS_CHECKERS = {
    "hh": _hh_connector_status,
}


@app.get("/api/v1/connectors/{name}/status", dependencies=[Depends(_require_key)])
def connector_status(name: str):
    checker = _CONNECTOR_STATUS_CHECKERS.get(name)
    if not checker:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {name}")
    return checker()


def _run_hh_login() -> dict:
    """Headless liveness check first (same _check_hh_live() session/start uses)
    — if the saved session is still genuinely live, skips the visible browser
    entirely instead of always forcing a fresh login window open even when
    nothing needs to change (user feedback: felt "crooked" always landing on
    an already-logged-in browser). Only if actually stale does the visible
    browser open, blocking until the user closes it, then saves cookies.
    Either way, a headless pass at the end refreshes the resume list.

    Loads any existing cookies into the fresh visible context before
    navigating (the old login.py never did this — every run was a blank
    slate, so re-link always forced a fresh login even when the saved session
    was still perfectly valid). Now: valid-but-not-yet-detected-as-such
    session → the window opens already signed in, the user just closes it;
    dead session → hh.ru itself bounces to the login page."""
    from playwright.sync_api import sync_playwright
    from config import CONFIG

    resumes_path = CONFIG.cookies_path.parent / "hh_resumes.json"
    already_connected = _check_hh_live()

    if not already_connected:
        existing_cookies = []
        if CONFIG.cookies_path.exists():
            try:
                existing_cookies = json.loads(CONFIG.cookies_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing_cookies = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            if existing_cookies:
                context.add_cookies(existing_cookies)
            page = context.new_page()
            page.goto("https://hh.ru", timeout=0, wait_until="commit")
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
            cookies = context.cookies()
            CONFIG.cookies_path.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()

        if not cookies:
            raise RuntimeError("No cookies captured — window was closed before signing in")

    resume_count = 0
    try:
        saved_cookies = json.loads(CONFIG.cookies_path.read_text(encoding="utf-8"))
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            ctx.add_cookies(saved_cookies)
            page = ctx.new_page()
            page.goto("https://hh.ru/applicant/resumes", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3_000)
            items = page.eval_on_selector_all(
                "a[href*='/resume/']",
                "els => els.map(e => ({href: e.href, text: e.innerText}))")
            seen: set = set()
            resumes = []
            for item in items:
                href = item["href"]
                if "/edit/" in href or "/visibility" in href:
                    continue
                m = re.search(r'/resume/([a-f0-9]{30,})', href)
                if not m:
                    continue
                uid = m.group(1)
                if uid in seen:
                    continue
                seen.add(uid)
                title = item["text"].split("\n")[0].strip() or "Resume"
                resumes.append({"title": title, "uuid": uid})
            browser.close()
        if resumes:
            resumes_path.write_text(json.dumps(resumes, ensure_ascii=False, indent=2), encoding="utf-8")
        resume_count = len(resumes)
    except Exception:
        pass  # resume-list refresh is a nice-to-have; cookie save above already succeeded

    return {"resume_count": resume_count, "already_connected": already_connected}


_CONNECTOR_LOGIN_RUNNERS = {
    "hh": _run_hh_login,
}


def _connector_login_worker(name: str) -> None:
    state = _connector_logins[name]
    state["state"] = "running"
    try:
        result = _CONNECTOR_LOGIN_RUNNERS[name]()
        state.update(state="done", error=None, **result)
    except Exception as exc:
        state.update(state="error", error=str(exc))


@app.post("/api/v1/connectors/{name}/login/start", dependencies=[Depends(_require_key)])
def connector_login_start(name: str):
    if name not in _CONNECTOR_LOGIN_RUNNERS:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {name}")
    current = _connector_logins.get(name)
    if current and current["state"] == "running":
        raise HTTPException(status_code=409, detail="Login already in progress")
    _connector_logins[name] = {"state": "starting", "error": None}
    t = threading.Thread(target=_connector_login_worker, args=(name,), daemon=True)
    t.start()
    return {"connector": name, "state": "starting"}


@app.get("/api/v1/connectors/{name}/login/status", dependencies=[Depends(_require_key)])
def connector_login_status(name: str):
    if name not in _CONNECTOR_LOGIN_RUNNERS:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {name}")
    return {"connector": name, **_connector_logins.get(name, {"state": "idle"})}


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

def _extract_headline(text: str) -> str:
    """First real content line — skips blank lines and the <!-- snaggd:start
    --> managed-block marker to_md() writes ahead of the actual '# Name'
    line. Bug found live 2026-07-15: with the marker as literal line 1, the
    old one-liner (`splitlines()[0]`) returned the marker text itself as the
    headline, for every profile, indistinguishably from every other."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        return stripped.lstrip("#").strip()
    return ""


def _profile_info(name: str, data_dir: Path) -> dict:
    """Build profile summary dict from profile directory."""
    info: dict = {"name": name, "data_dir": str(data_dir), "configured": False}
    candidate = data_dir / "candidate.md"
    if candidate.exists():
        info["configured"] = True
        info["candidate_headline"] = _extract_headline(candidate.read_text(encoding="utf-8"))
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
    info = _profile_info(name, data_dir)
    # Full candidate.json, not just _profile_info's headline/stats — only on
    # the single-profile route (GET /api/v1/profiles' list stays lean), so the
    # GUI wizard can prefill a re-run instead of starting blank.
    candidate_path = data_dir / "candidate.json"
    info["candidate"] = None
    if candidate_path.exists():
        try:
            info["candidate"] = json.loads(candidate_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Raw rendered text too — ProfileTab's markdown pane shows this directly,
    # not a re-render of the candidate dict above.
    md_path = data_dir / "candidate.md"
    info["candidate_md"] = md_path.read_text(encoding="utf-8") if md_path.exists() else None
    # Live filters.json values, not candidate["rules"] above — that copy isn't read by the
    # apply loop (see wizard.py Step 6 comment); this is what adapter.py actually enforces.
    stop_filters = load_stop_filters(data_dir)
    info["min_match"] = stop_filters.min_match
    info["min_employer_rating"] = stop_filters.min_employer_rating
    return info


@app.post("/api/v1/profiles/{name}/min-match", dependencies=[Depends(_require_key)])
def profile_min_match_patch(name: str, req: MinMatchPatchRequest):
    data_dir = PROFILES_DIR / name
    if not data_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    patch_filters_json(data_dir, min_match=req.min_match)
    return {"updated": True, "min_match": req.min_match}


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
    import shutil

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
    json_out = data_dir / "candidate.json"

    # profile is now user-chosen (GUI wizard, see project_multiprofile_architecture
    # memory) instead of a fixed 'demo' target, so a save can land on a profile
    # with real history behind it — back up whatever was already there before
    # overwriting. One timestamp shared by both files so a given save's backup
    # is recoverable as a matched pair, not two independently-timed halves.
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    for existing in (md_out, json_out):
        if existing.exists():
            shutil.copy2(existing, existing.with_name(existing.name + f".{stamp}.bak"))

    md_out.write_text(rendered_md, encoding="utf-8")

    payload = dataclasses.asdict(data)
    payload.pop("suggested_queries", None)  # parser convenience field, not part of the schema
    payload.pop("career_profile_suggestions", None)  # same — wizard-prefill only, not the schema
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"saved": True, "profile": req.profile}


if __name__ == "__main__":
    # Lets this file run directly (`python api.py` / a PyInstaller-frozen build of
    # it) in addition to the `uvicorn api:app` dev invocation in the module
    # docstring — a frozen build has no external `uvicorn` CLI to shell out to.
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
