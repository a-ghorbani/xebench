#!/usr/bin/env bash
# Usage: scripts/capture_app_run.sh <serial> [timeout_sec]
# Raw records are local diagnostics, not sanitized public evidence.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/capture_app_run.py "$@"
