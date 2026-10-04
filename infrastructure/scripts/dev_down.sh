#!/usr/bin/env bash
# dev_down.sh — stop everything dev_up.sh started (job runner, backend, dev DB).
# Safe to run repeatedly; only touches processes with PIDs in ./.run/.
set -euo pipefail
cd "$(dirname "$0")/../.."
RUN=.run

for n in jobs backend dev_db; do
  f="$RUN/$n.pid"
  [ -f "$f" ] || continue
  pid=$(cat "$f")
  if kill -0 "$pid" 2>/dev/null; then
    echo "[dev-down] stopping $n (pid $pid)"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && { echo "[dev-down] force-killing $n"; kill -9 "$pid" 2>/dev/null || true; }
  else
    echo "[dev-down] $n not running (stale pid $pid)"
  fi
  rm -f "$f"
done

# Git Bash may leave the uvicorn --reload parent's children behind; mop up by port.
if (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null; then
  echo "[dev-down] WARNING: something still listens on :8000 (not ours?); check: netstat -ano | grep :8000"
fi
echo "[dev-down] DONE. Logs kept in $RUN/*.log"
