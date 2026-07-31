import json
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from config import CONFIG


def _extract_vacancy_id(url: str) -> Optional[str]:
    """Extracts numeric vacancy ID from any HH URL variant (canonical or relative)."""
    if not url:
        return None
    m = re.search(r'/vacancy/(\d+)', url)
    return m.group(1) if m else None

class Logger:
    def __init__(self, applied_log_path: Path = None, logs_dir: Path = None,
                 dedup_source_path: Path = None):
        self.applied_log_path = applied_log_path or CONFIG.applied_log_path
        self.logs_dir = logs_dir or CONFIG.logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.daily_log_path = self._get_daily_log_path()
        # Debug/dev runs write to a separate applied_log_path (kept out of the
        # real stats) but must still see real history for dedup — otherwise a
        # debug rerun could re-apply to a vacancy already really applied to.
        # dedup_source_path is that read-only extra source; load_applied_log()
        # merges it in front, save_applied_log() strips it back off before
        # writing, so this logger's own file only ever contains its own entries.
        self.dedup_source_path = dedup_source_path
        self._dedup_baseline_count = 0

    def _get_daily_log_path(self) -> Path:
        """Returns path to today's log file."""
        today = datetime.now().strftime('%Y-%m-%d')
        return self.logs_dir / f"{today}.log"

    def _read_json_list(self, path: Path) -> List[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def load_applied_log(self) -> List[Dict[str, Any]]:
        """Loads this logger's own applied_log.json, merged with the read-only
        dedup_source_path (if set) so callers get one list to dedup against.
        The merged prefix's length is remembered so save_applied_log() can
        strip it back off — see class docstring above."""
        own = self._read_json_list(self.applied_log_path)
        if not self.dedup_source_path or self.dedup_source_path == self.applied_log_path:
            self._dedup_baseline_count = 0
            return own
        extra = self._read_json_list(self.dedup_source_path)
        self._dedup_baseline_count = len(extra)
        return extra + own

    def save_applied_log(self, log_data: List[Dict[str, Any]]) -> None:
        """Saves applied_log.json — only the portion beyond the read-only
        dedup baseline (see load_applied_log) when dedup_source_path is set."""
        to_save = log_data[self._dedup_baseline_count:] if self.dedup_source_path else log_data
        with open(self.applied_log_path, "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
    
    def is_processed(self, url: str, applied_log: List[Dict[str, Any]]) -> Optional[str]:
        """Returns existing status if vacancy was already processed, else None.

        Matches by vacancy ID extracted from URL so that tracking URLs
        (adsrv.hh.ru/click?...&meta=SESSION_HASH) match canonical log entries
        (hh.ru/vacancy/12345678) across sessions.
        Falls back to exact URL match for entries without a parseable vacancy ID.
        """
        query_id = _extract_vacancy_id(url)
        for entry in applied_log:
            status = entry.get("status")
            if status in ("dry_run", "skipped_llm_unavailable", "needs_debug_review"):
                continue  # retryable: scored-only, transient LLM failure, or ambiguous execution failure
            if entry.get("url") == url:
                return status
            if query_id:
                stored_id = entry.get("vacancy_id") or _extract_vacancy_id(entry.get("url", ""))
                if stored_id == query_id:
                    return status
        return None
    
    def log_result(self, applied_log: List[Dict[str, Any]], **kwargs) -> None:
        """Appends an entry to applied_log and persists it."""
        entry = {
            "date": datetime.now().isoformat(),
            **kwargs
        }
        applied_log.append(entry)
        self.save_applied_log(applied_log)
    
    def log_daily(self, message: str) -> None:
        """Appends a timestamped message to the daily log."""
        timestamp = datetime.now().strftime('%H:%M:%S')
        with open(self.daily_log_path, 'a', encoding='utf-8') as f:
            f.write(f"{timestamp} {message}\n")
    
    def log_session_summary(self, processed_count: int, successful: int, skipped: int, new_entries: List[Dict]) -> None:
        """Writes session summary to the daily log."""
        self.log_daily(f"\n=== SESSION {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
        self.log_daily(f"Processed: {processed_count}, Successful: {successful}, Skipped: {skipped}")
        
        for entry in new_entries:
            title = entry.get('title', 'N/A')[:50]
            url = entry.get('url', 'N/A')[:60]
            status = entry.get('status', 'unknown')
            self.log_daily(f"  {status}: {title} - {url}")
        
        self.log_daily(f"applied_log.json path: {self.applied_log_path}")
        self.log_daily(f"Daily log path: {self.daily_log_path}")
    
    def count_session_results(self, applied_log: List[Dict[str, Any]], initial_count: int) -> "tuple[int, int]":
        """Counts successful and skipped results for entries added in this session."""
        new_entries = applied_log[initial_count:]
        successful = sum(1 for e in new_entries if e.get('status', '').startswith('applied'))
        skipped = sum(1 for e in new_entries if 'skipped' in e.get('status', ''))
        return successful, skipped