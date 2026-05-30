"""
LLM model benchmark — compare scoring + cover quality across models.

Reads today's applied vacancies from applied_log.json, re-fetches each vacancy
text via HHBrowser, runs score_vacancy + generate_cover with the configured model,
then prints full side-by-side comparison and saves results to /tmp/benchmark_out.json.

Does NOT write to applied_log.json.

Usage:
    # Compare against today's applied entries using a specific model:
    LLM_MODEL=anthropic/claude-haiku-4.5 COVER_MODEL=anthropic/claude-haiku-4.5 \
        python scripts/benchmark_models.py

    # Specify a date and output path:
    python scripts/benchmark_models.py --date 2026-05-30 --out /tmp/my_benchmark.json

Verified model IDs (OpenRouter):
    deepseek/deepseek-v3.2          (default, ~$0.002/vacancy)
    anthropic/claude-haiku-4.5      (works via OpenRouter for RU, ~$0.01/vacancy)
    google/gemini-2.0-flash-lite    (~$0.0004/vacancy)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Must set env vars before project imports — Config/LLMAgent read them at instantiation
_benchmark_model = os.environ.get("LLM_MODEL", os.environ.get("_BENCH_MODEL", "deepseek/deepseek-v3.2"))
os.environ.setdefault("LLM_MODEL", _benchmark_model)
os.environ.setdefault("COVER_MODEL", _benchmark_model)

from config import CONFIG
from core.llm_agent import LLMAgent
from adapters.hh.browser import HHBrowser

parser = argparse.ArgumentParser(description="LLM model benchmark on applied vacancies")
parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Filter date (YYYY-MM-DD)")
parser.add_argument("--out", default="/tmp/benchmark_out.json", help="Output JSON path")
args = parser.parse_args()

LOG_PATH = ROOT / "data" / "applied_log.json"
if not LOG_PATH.exists():
    print(f"ERROR: applied_log.json not found at {LOG_PATH}")
    sys.exit(1)

with open(LOG_PATH) as f:
    log = json.load(f)

targets = [
    e for e in log
    if e.get("date", "").startswith(args.date) and "applied" in e.get("status", "")
]

model_label = os.environ["LLM_MODEL"]

print(f"\n{'='*65}")
print(f"  Benchmark — {len(targets)} vacancies for {args.date}")
print(f"  Model:  {model_label}")
print(f"{'='*65}\n")

if not targets:
    print("No applied vacancies found for this date. Exiting.")
    sys.exit(0)

browser = HHBrowser()
browser.start(debug=False)

agent = LLMAgent()
results = []

for i, entry in enumerate(targets, 1):
    url = entry.get("url") or f"https://hh.ru/vacancy/{entry.get('vacancy_id')}"
    title = entry.get("title", "?")
    print(f"\n[{i}/{len(targets)}] {title[:60]}")
    print(f"  URL: {url}")

    if not browser.open_vacancy(url):
        print("  ❌ Failed to open")
        results.append({"url": url, "title": title, "error": "open_failed"})
        continue

    text = browser.get_vacancy_text()
    if not text:
        print("  ❌ No text extracted")
        browser.close_vacancy()
        results.append({"url": url, "title": title, "error": "no_text"})
        continue

    score_data = agent.score_vacancy(text)
    cover_text = agent.generate_cover(text, match_context=score_data)

    bench_score   = score_data.get("score")
    bench_signals = score_data.get("signals", [])
    bench_skills  = score_data.get("matched_skills", [])
    bench_gaps    = score_data.get("gaps", [])

    log_score   = entry.get("match_score")
    log_signals = entry.get("signals", [])
    log_cover   = entry.get("cover_letter", "")
    log_skills  = entry.get("matched_skills", [])

    print(f"\n  Log entry  score={log_score}  signals={log_signals}")
    print(f"  Benchmark  score={bench_score}  signals={bench_signals}")
    print(f"\n  --- Log cover ---\n{log_cover}")
    print(f"\n  --- Benchmark cover ({model_label}) ---\n{cover_text}")
    print(f"\n  Skills +benchmark: {set(bench_skills) - set(log_skills)}")
    print(f"  Skills +log:       {set(log_skills) - set(bench_skills)}")

    results.append({
        "title":  title,
        "url":    url,
        "log": {
            "score":   log_score,
            "signals": log_signals,
            "skills":  log_skills,
            "gaps":    entry.get("gaps", []),
            "cover":   log_cover,
        },
        "benchmark": {
            "model":   model_label,
            "score":   bench_score,
            "signals": bench_signals,
            "skills":  bench_skills,
            "gaps":    bench_gaps,
            "cover":   cover_text,
        },
    })

    browser.close_vacancy()
    time.sleep(2)

browser.close()

out = Path(args.out)
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\n{'='*65}")
print(f"  Done. Full results → {out}")
print(f"{'='*65}\n")
