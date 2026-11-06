#!/usr/bin/env bash
# Launcher: prefer the built single-file executable; fall back to the source.
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x dist/download-manager ]]; then
    exec ./dist/download-manager "$@"
elif [[ -x venv/bin/python ]]; then
    exec venv/bin/python download_manager_gui.py "$@"
else
    exec python3 download_manager_gui.py "$@"
fi