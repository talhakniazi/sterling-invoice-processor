#!/usr/bin/env bash
# macOS / Linux launcher. First run installs dependencies into .venv (needs internet).
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "First-time setup: installing components..."
  python3 -m venv .venv || { echo "Python 3 is not installed. See the Installation Guide."; exit 1; }
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt || { echo "Install failed - check internet connection."; rm -rf .venv; exit 1; }
fi
exec .venv/bin/python "Invoice Processor.pyw"
