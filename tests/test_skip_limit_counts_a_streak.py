"""max_skips ends a run on a STREAK of skips, not on a total.

The stop line has always said "N consecutive skips" and the GUI "Too many
skips in a row" — and the counter behind them never reset. On 2026-09-14 a
run asked for 15 applications, made 10, and stopped after its tenth skip,
the ten spread evenly among the ten applications. At a 50% miss rate every
run died at the twentieth vacancy, five short, with a message that named a
streak nobody had seen.

Driven through run() on stubs: process_vacancy alternates skip / applied.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

os.environ.setdefault("LLM_API_KEY", "test")

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


with patch("core.llm_agent.OpenAI"):
    from adapters.hh.adapter import HHAdapter
    from config import CONFIG


class _Logger:
    def __init__(self): self.daily = []
    def load_applied_log(self): return []
    def log_daily(self, message): self.daily.append(message)
    def is_processed(self, url, log): return None
    def log_result(self, log, **kwargs): log.append(kwargs)


def _run(pattern, n_vacancies, max_vacancies):
    """pattern: a string of 's' (skip) and 'a' (applied), repeated."""
    with tempfile.TemporaryDirectory() as tmp:
        with patch("core.llm_agent.OpenAI"):
            a = HHAdapter(data_dir=Path(tmp))
        a.get_vacancies = lambda target_url=None: [
            (f"https://hh.ru/vacancy/{i}", f"v{i}", i + 1, "search") for i in range(n_vacancies)]
        a.browser.get_current_page = lambda: None
        calls = {"n": 0}

        def process(url, title, index, llm_cover, **kw):
            kind = pattern[calls["n"] % len(pattern)]; calls["n"] += 1
            if kind == "s":
                return {"status": "skipped_score", "reason": "low", "scenario": "skip"}
            return {"status": "applied_via_chat", "reason": "ok", "scenario": "chat_cover_sent",
                    "details": {}}
        a.process_vacancy = process
        logger = _Logger()
        with patch.object(a, "_close_ledger"):
            a.run(logger, max_vacancies=max_vacancies)
        ended = next((m for m in logger.daily if "Session ended" in m), "")
        return ended, calls["n"]


limit = CONFIG.max_skips
ended, seen = _run("sa", n_vacancies=limit * 6, max_vacancies=limit * 2)
check("skips interleaved with applications never trip the streak limit",
      "max_skips_reached" not in ended)
check("and the run reaches the application count it was asked for",
      "max_vacancies_reached" in ended)

ended, seen = _run("s", n_vacancies=limit * 3, max_vacancies=5)
check("a genuine streak still stops the run", "max_skips_reached" in ended)
check("at exactly the limit", seen == limit)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
