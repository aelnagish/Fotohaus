#!/bin/bash
# Run Fotohaus straight from the source code (no DMG needed).
#   ./run.sh                 open the library
#   ./run.sh photo.jpg       open a photo in the Photo Viewer
# The first run sets up a private Python environment in .venv (takes a few minutes).
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  PY=$(command -v python3.12 || command -v python3.13 || command -v python3.11 || command -v python3)
  echo "Setting up Python environment with $PY ..."
  "$PY" -m venv .venv
  .venv/bin/pip install --upgrade pip -q
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python -m fotohaus "$@"
