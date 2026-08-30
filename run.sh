#!/usr/bin/env bash
# Jalankan server transkrip
cd "$(dirname "$0")" || exit 1
[ -d .venv ] || { echo "Membuat virtualenv..."; python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
exec .venv/bin/python app.py "$@"
