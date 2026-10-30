#!/usr/bin/env bash
# Run pytest without relying on uv's editable-install .pth files.
#
# On macOS the repository lives under a synced directory that keeps re-applying the
# UF_HIDDEN file flag to .venv/lib/python3.13/site-packages/*.pth, and Python 3.13
# silently skips hidden .pth files, so every workspace package becomes unimportable.
# Putting the workspace source roots on PYTHONPATH sidesteps the flag.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHONPATH="$(printf '%s\n' packages/*/src services/*/src | paste -sd: -)"
export PYTHONPATH

exec .venv/bin/python -m pytest "$@"
