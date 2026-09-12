"""A vacancy whose page never loaded is not retired.

Page.goto timed out twice in two days (2026-09-10 #2, 2026-09-11 #5), both
times while a VPN was dropping, and both vacancies were marked processed for
good: skipped_open_error was not on is_processed's retryable list. The list's
own argument — a vacancy that was never judged must not be retired on the
strength of a failure to measure it — applies one step earlier here: this one
was never even seen.
"""
import sys
import tempfile
from pathlib import Path

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

from logger import Logger

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    lg = Logger(applied_log_path=root / "applied_log.json", logs_dir=root / "logs")
    log = [
        {"url": "https://odintsovo.hh.ru/vacancy/137198079?hhtmFrom=vacancy_search_list",
         "status": "skipped_open_error", "reason": "Failed to open vacancy"},
        {"url": "https://odintsovo.hh.ru/vacancy/137184616", "vacancy_id": "137184616",
         "status": "applied_via_chat"},
    ]
    check("a page that never loaded is offered again next run",
          lg.is_processed("https://hh.ru/vacancy/137198079", log) is None)
    check("while an application that went out stays processed",
          lg.is_processed("https://hh.ru/vacancy/137184616", log) == "applied_via_chat")

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
