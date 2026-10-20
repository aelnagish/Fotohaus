#!/bin/bash
# Run Fotohaus straight from the source code (no DMG needed).
#   ./run.sh                 open the library
#   ./run.sh photo.jpg       open a photo in the Photo Viewer
# The first run sets up a private Python environment in .venv (takes a few minutes); later runs
# only install something when requirements.txt has changed.
set -e
# photos/folders given relative to where you ran this keep working after the cd below
args=()
for a in "$@"; do
  if [ -e "$a" ] && [ "${a#/}" = "$a" ]; then a="$PWD/$a"; fi
  args+=("$a")
done
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  PY=$(command -v python3.12 || command -v python3.13 || command -v python3.11 || command -v python3)
  echo "Setting up Python environment with $PY ..."
  "$PY" -m venv .venv
  .venv/bin/pip install --upgrade pip -q
fi
want=$(shasum requirements.txt | cut -d' ' -f1)
if [ "$(cat .venv/.requirements 2>/dev/null)" != "$want" ]; then
  echo "Installing updated requirements ..."
  .venv/bin/pip install -q -r requirements.txt
  echo "$want" > .venv/.requirements
fi
exec .venv/bin/python -m fotohaus "${args[@]}"
