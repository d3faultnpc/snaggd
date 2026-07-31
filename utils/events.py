"""In-memory event feed for one API-driven session — an attached GUI client's live log.

CLI runs (main.py) never construct a reporter: adapter call sites take
Optional[EventReporter] and fall back to plain print, so stdout there is
byte-identical with or without this module in play. api.py owns the lifecycle
(one reporter per _sessions entry); the engine only ever receives it as an
opaque collaborator — no import of api.py internals in either direction.
"""

import threading
from collections import deque
from datetime import datetime


class EventReporter:
    """Rolling, thread-safe buffer of structured narration events.

    Producer is the session's background thread (adapter/_session_worker);
    consumers are FastAPI worker threads polling /session/{id}/events — hence
    the lock (deque appends are atomic, but since() iterates while the
    producer may append). maxlen bounds memory across arbitrarily long runs;
    `seq` is monotonic and survives eviction, so a polling client can cursor
    with `after=` and never re-read or mis-order, even after old events fall
    off the left end.
    """

    def __init__(self, maxlen: int = 500):
        self._events = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0

    def emit(
        self,
        message: str,
        level: str = "info",
        actor: str = "scan",
        vacancy_id: str | None = None,
        company: str | None = None,
        position: str | None = None,
    ) -> None:
        """actor: "scan" (Playwright/mechanics — the mechanical action log) or
        "llm" (LLM output — a separate narration display). Defaults to "scan"
        since nearly every existing call site is the scan actor's own
        pipeline narration; call sites that represent LLM output (scoring,
        cover generation, form-answer generation) pass actor="llm" explicitly.
        vacancy_id/company/position: which vacancy this event belongs to, if
        any — None means a session-level event (login, pause/resume/stop,
        search summary), not nested under any specific vacancy. The consumer
        (Terminal) groups by vacancy_id and renders vacancy_id=None events as
        standalone lines instead.
        All four are additive/optional — every pre-existing call site with
        just (message) or (message, level=...) behaves exactly as before.
        """
        with self._lock:
            self._seq += 1
            self._events.append({
                "seq": self._seq,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "level": level,
                "message": message,
                "actor": actor,
                "vacancy_id": vacancy_id,
                "company": company,
                "position": position,
            })

    def since(self, after: int = 0):
        """(events with seq > after, oldest first; current last seq)."""
        with self._lock:
            return [e for e in self._events if e["seq"] > after], self._seq
