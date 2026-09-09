"""A failed write must not destroy the applied log.

save_applied_log() used to open the real file "w" — truncating it — and then
spend ~45 ms refilling it, once per logged vacancy. Anything that ended the
process inside that window left half-written JSON on disk, and _read_json_list
turns a JSONDecodeError into an empty list in silence. The next run would see
no history: dedup resets, and the crawler re-walks every vacancy already
applied to. The merge of the two archived profiles on 2026-09-09 took this file
from 434 KB to 1.2 MB and widened that window threefold, which is what made it
worth closing.

These tests fail against the "w"-in-place version and pass against the
write-then-rename one.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import Logger

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


GOOD = [{"date": "2026-09-01T00:00:00", "url": "https://hh.ru/vacancy/1", "status": "applied"},
        {"date": "2026-09-02T00:00:00", "url": "https://hh.ru/vacancy/2", "status": "skipped_score"}]

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    log_path = root / "applied_log.json"
    lg = Logger(applied_log_path=log_path, logs_dir=root / "logs")

    lg.save_applied_log(GOOD)
    check("a normal save round-trips", json.loads(log_path.read_text(encoding="utf-8")) == GOOD)
    check("no temp file is left behind", not (root / "applied_log.json.tmp").exists())

    # The whole point: a write that dies partway must leave the old file intact.
    def die_partway(obj, fp, **kw):
        fp.write('[\n  {"date": "2026-09-03T00:00:00", "url": "https://hh.ru/vac')
        raise KeyboardInterrupt("process ended mid-write")

    try:
        with patch("logger.json.dump", die_partway):
            lg.save_applied_log(GOOD + [{"date": "2026-09-03T00:00:00", "status": "applied"}])
    except KeyboardInterrupt:
        pass

    on_disk = log_path.read_text(encoding="utf-8")
    check("the log on disk is still valid JSON after a killed write",
          json.loads(on_disk) == GOOD)
    check("…and the killed write is not visible in it", "2026-09-03" not in on_disk)
    check("a reader gets the history back, not an empty list",
          len(Logger(applied_log_path=log_path, logs_dir=root / "logs").load_applied_log()) == 2)

    # The failure this replaces: an empty log silently un-dedups everything.
    lg2 = Logger(applied_log_path=log_path, logs_dir=root / "logs")
    log = lg2.load_applied_log()
    check("dedup still recognises a vacancy applied to before the killed write",
          lg2.is_processed("https://hh.ru/vacancy/1", log) == "applied")

    # dedup_source_path callers must keep writing only their own portion.
    src = root / "source.json"
    src.write_text(json.dumps(GOOD), encoding="utf-8")
    own = root / "debug_log.json"
    lg3 = Logger(applied_log_path=own, logs_dir=root / "logs", dedup_source_path=src)
    merged = lg3.load_applied_log()
    merged.append({"date": "2026-09-04T00:00:00", "status": "dry_run"})
    lg3.save_applied_log(merged)
    check("the read-only dedup baseline is still stripped before writing",
          json.loads(own.read_text(encoding="utf-8")) == [{"date": "2026-09-04T00:00:00",
                                                           "status": "dry_run"}])
    check("…and the dedup source is untouched",
          json.loads(src.read_text(encoding="utf-8")) == GOOD)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
