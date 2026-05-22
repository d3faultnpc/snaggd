#!/usr/bin/env python3
"""
Pre-publish sensitive data check.
Run before any git push: python scripts/check_sensitive.py

Exit 0 = clean. Exit 1 = issues found, do NOT push.
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).parent.parent

# Files and dirs to skip entirely
SKIP_DIRS = {"venv", ".venv", "__pycache__", ".git", "debug_screenshots",
             "logs", "data", ".claude", "_docs", "sandbox"}
SKIP_FILES = {".env", "DEVLOG.md"}  # gitignored files — never pushed
SKIP_EXTENSIONS = {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".ico",
                   ".pdf", ".docx", ".zip"}

# Patterns that indicate sensitive data
SENSITIVE_PATTERNS = [
    # Hardcoded absolute paths with real username
    (r"/Users/[a-zA-Z][a-zA-Z0-9_-]+/", "hardcoded user path"),
    # Cookie-like tokens (long alphanumeric strings in quotes)
    (r'["\']([a-f0-9]{32,})["\']', "possible cookie/token value"),
    # API keys (common formats)
    (r'sk-[a-zA-Z0-9]{20,}', "possible API key (sk-)"),
    (r'Bearer [a-zA-Z0-9\-._~+/]{20,}', "possible Bearer token"),
    # Russian phone numbers
    (r'\+7[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}', "phone number"),
    # Email addresses (in code files, not in templates)
    (r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', "email address"),
    # Resume ID in HH search URL
    (r'resume=[a-f0-9]{30,}', "HH resume ID in URL"),
]

# Patterns allowed in specific contexts (whitelist)
ALLOWED_PATTERNS = [
    r'\.env\.example',       # .env.example file itself
    r'check_sensitive\.py',  # this script
    r'\.gitignore',          # gitignore mentions data paths
    r'CONTEXT\.md',          # architecture docs mention patterns
    r'README\.md',           # readme may have examples
    r'd3faultnpc@proton\.me',  # deliberately not whitelisting — should not be in code
]


def should_skip(path: Path) -> bool:
    if path.name in SKIP_FILES:
        return True
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
    if path.suffix in SKIP_EXTENSIONS:
        return True
    return False


def check_file(path: Path) -> List[Tuple[int, str, str]]:
    issues = []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return issues

    for lineno, line in enumerate(content.splitlines(), 1):
        for pattern, label in SENSITIVE_PATTERNS:
            matches = re.findall(pattern, line)
            if matches:
                # Check if this file is in allowed list
                path_str = str(path)
                if any(re.search(ap, path_str) for ap in ALLOWED_PATTERNS):
                    continue
                issues.append((lineno, label, line.strip()[:120]))
    return issues


def main():
    print("Scanning for sensitive data...\n")
    total_issues = 0
    files_checked = 0

    for path in sorted(ROOT.rglob("*")):
        if path.is_dir() or should_skip(path):
            continue
        files_checked += 1
        issues = check_file(path)
        if issues:
            rel = path.relative_to(ROOT)
            print(f"  {rel}")
            for lineno, label, snippet in issues:
                print(f"    L{lineno} [{label}]: {snippet}")
                total_issues += 1
            print()

    print(f"Checked {files_checked} files.")
    if total_issues == 0:
        print("No sensitive data found. Safe to push.")
        return 0
    else:
        print(f"\n{total_issues} issue(s) found. DO NOT push until resolved.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
