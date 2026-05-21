import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from config import CONFIG

class Logger:
    def __init__(self):
        self.applied_log_path = CONFIG.applied_log_path
        self.logs_dir = CONFIG.logs_dir
        self.daily_log_path = self._get_daily_log_path()
        
    def _get_daily_log_path(self) -> Path:
        """Returns path to today's log file."""
        today = datetime.now().strftime('%Y-%m-%d')
        return self.logs_dir / f"{today}.log"
    
    def load_applied_log(self) -> List[Dict[str, Any]]:
        """Loads applied_log.json."""
        try:
            with open(self.applied_log_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
    
    def save_applied_log(self, log_data: List[Dict[str, Any]]) -> None:
        """Saves applied_log.json."""
        with open(self.applied_log_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
    
    def is_processed(self, url: str, applied_log: List[Dict[str, Any]]) -> Optional[str]:
        """Returns existing status string if vacancy was already processed, else None."""
        for entry in applied_log:
            if entry.get("url") == url:
                return entry.get("status")
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
    
    def count_session_results(self, applied_log: List[Dict[str, Any]], initial_count: int) -> tuple[int, int]:
        """Counts successful and skipped results for entries added in this session."""
        new_entries = applied_log[initial_count:]
        successful = sum(1 for e in new_entries if e.get('status', '').startswith('applied'))
        skipped = sum(1 for e in new_entries if 'skipped' in e.get('status', ''))
        return successful, skipped