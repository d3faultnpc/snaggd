#!/usr/bin/env bash
# (Re)creates venv/ with a Python new enough for this project's pinned dependencies.
# Exists because macOS ships an ancient /usr/bin/python3 (3.8.x) that some shells put
# first on PATH ahead of a modern brew-installed python3.x — bare `python3 -m venv`
# can silently pick that one, and `pip install -r requirements.txt` then fails with
# misleading "version not found" errors for pins that are perfectly valid.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    minor="$("$candidate" -c 'import sys; print(sys.version_info[1])')"
    major="$("$candidate" -c 'import sys; print(sys.version_info[0])')"
    if [ "$major" -eq 3 ] && [ "$minor" -ge 9 ]; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "No Python >=3.9 found (checked python3.13/3.12/3.11/3.10/python3)." >&2
  echo "On macOS, /usr/bin/python3 is often a very old Apple-bundled build." >&2
  echo "Install a modern one, e.g.: brew install python@3.12" >&2
  exit 1
fi

echo "Using $PYTHON ($("$PYTHON" --version))"
rm -rf venv
"$PYTHON" -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
echo "Done. Activate with: source venv/bin/activate"
