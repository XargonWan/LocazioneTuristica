#!/usr/bin/env bash
# Simple helper script to build and run LocazioneTuristica app
# Usage: ./scripts/run.sh dev|docker|docker-detached|stop|seed|help

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$ROOT_DIR"

usage() {
  cat <<EOF
Usage: $0 <command>
Commands:
  dev              Create venv (if missing), install deps, seed DB, and start uvicorn (dev mode --reload)
  docker           Build and run docker-compose in foreground
  docker-detached  Build and run docker-compose in detached mode
  stop             Stop docker-compose services or kill dev uvicorn started by this script (if any)
  seed             Run DB seed script to create admin & default settings
  help             Show this help
EOF
}

ensure_venv() {
  if [ ! -d ".venv" ]; then
    echo "Creating virtualenv .venv..."
    if command -v python3.14 >/dev/null 2>&1; then
      python3.14 -m venv .venv
    else
      python3 -m venv .venv
    fi
  fi
  # Activate
  # shellcheck source=/dev/null
  source .venv/bin/activate
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
}

seed_db() {
  # Ensure venv exists for running seed
  if [ ! -d ".venv" ]; then
    ensure_venv
  fi
  # shellcheck source=/dev/null
  source .venv/bin/activate
  PYTHONPATH="$ROOT_DIR" .venv/bin/python scripts/seed.py
}

start_dev() {
  ensure_venv
  mkdir -p data/attachments
  seed_db
  echo "Starting uvicorn (dev mode) on http://0.0.0.0:8000 - use Ctrl+C to stop"
  # Run in foreground for logs. If the user backgrounds it, we also write a pid file so stop() can find it.
  LOC_AZIONE_NAME=locazioneturistica .venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
  uv_pid=$!
  echo "Started uvicorn (pid=$uv_pid)"
  # if started via & (background), write a pid file so stop will target the right process
  if [ -n "$uv_pid" ]; then
    echo "$uv_pid" > ".dev_uvicorn.pid"
    wait $uv_pid
    rm -f ".dev_uvicorn.pid" 2>/dev/null || true
  fi
}

start_docker() {
  mkdir -p data/attachments
  docker compose up --build
}

start_docker_detached() {
  mkdir -p data/attachments
  docker compose up -d --build
}

stop_services() {
  if docker compose ps >/dev/null 2>&1; then
    echo "Stopping docker-compose services..."
    docker compose down
  fi
  # Attempt to find and kill any uvicorn/python process listening on port 8000 to avoid "address already in use"
  echo "Checking for dev server processes listening on port 8000..."
  PIDS=""
  # Prefer ss to find listeners
  if command -v ss >/dev/null 2>&1; then
    # ss output format: 'LISTEN 0 128 127.0.0.1:8000 *:* users:("python",pid=1234,fd=3)'
    while read -r line; do
      pid=$(echo "$line" | sed -n 's/.*pid=\([0-9]*\),.*/\1/p') || true
      if [ -n "$pid" ]; then
        PIDS="$PIDS $pid"
      fi
    done < <(ss -ltnp 2>/dev/null | grep -E ":8000 .*users:" || true)
  fi
  # Try lsof
  if [ -z "$PIDS" ] && command -v lsof >/dev/null 2>&1; then
    p=$(lsof -ti :8000 || true)
    if [ -n "$p" ]; then
      PIDS="$PIDS $p"
    fi
  fi
  # Fallback to pgrep for uvicorn/python
  if [ -z "$PIDS" ]; then
    p=$(pgrep -f "uvicorn" || true)
    if [ -n "$p" ]; then
      PIDS="$PIDS $p"
    fi
  fi
  if [ -n "$PIDS" ]; then
    echo "Found candidate process(es):$PIDS"
    # Read pidfile if present and prefer targeting it
    if [ -f ".dev_uvicorn.pid" ]; then
      pidfile_pid=$(cat .dev_uvicorn.pid || true)
      if [ -n "$pidfile_pid" ]; then
        echo "Found PID file for dev server: $pidfile_pid"
        if echo "$PIDS" | grep -w "$pidfile_pid" >/dev/null 2>&1; then
          PIDS="$pidfile_pid"
        fi
      fi
    fi
    echo "Killing processes:$PIDS"
    for pid in $PIDS; do
      # get full cmdline and check for project root, uvicorn and app.main
      cmdline=$(ps -o args= -p "$pid" 2>/dev/null || true)
      should_kill=0
      # If pidfile exists and matches, prefer that
      if [ -f ".dev_uvicorn.pid" ]; then
        pidfile_pid=$(cat .dev_uvicorn.pid || true)
        if [ -n "$pidfile_pid" ] && [ "$pid" = "$pidfile_pid" ]; then
          should_kill=1
        fi
      fi
      # Check env var marker if available
      if [ "$should_kill" -eq 0 ] && [ -f "/proc/$pid/environ" ]; then
        if tr '\0' '\n' < /proc/$pid/environ | grep -Fq "LOC_AZIONE_NAME=locazioneturistica" >/dev/null 2>&1; then
          should_kill=1
        fi
      fi
      # Check cwd for this project root
      if [ "$should_kill" -eq 0 ] && [ -d "/proc/$pid/cwd" ]; then
        cwd=$(readlink -f /proc/$pid/cwd 2>/dev/null || true)
        if [ "$cwd" = "$ROOT_DIR" ]; then
          should_kill=1
        fi
      fi
      # Check command line for uvicorn and app.main:app
      if [ "$should_kill" -eq 0 ]; then
        if echo "$cmdline" | grep -E "app.main:app|uvicorn|python" >/dev/null 2>&1; then
          # Last chance: if cmdline contains host/port for 8000, it's likely ours
          if echo "$cmdline" | grep -E "(--port[= ]?8000|:8000|127\.0\.0\.1|0\.0\.0\.0)" >/dev/null 2>&1; then
            should_kill=1
          fi
        fi
      fi
      if [ "$should_kill" -eq 1 ]; then
        echo "Terminating PID $pid ($cmdline)";
        kill -TERM "$pid" 2>/dev/null || true
        sleep 0.1
        kill -KILL "$pid" 2>/dev/null || true
      else
        echo "Skipping PID $pid (not recognized as this project's process): $cmdline"
      fi
    done
  else
    echo "No dev server processes found on port 8000."
  fi
  # Cleanup pidfile if found and process no longer exists
  if [ -f ".dev_uvicorn.pid" ]; then
    pf=$(cat .dev_uvicorn.pid || true)
    if [ -n "$pf" ] && ! ps -p "$pf" >/dev/null 2>&1; then
      echo "Removing stale pidfile .dev_uvicorn.pid (pid $pf not running)"
      rm -f .dev_uvicorn.pid || true
    fi
  fi
}

if [ $# -eq 0 ]; then
  echo "No command passed, starting development server (default). To see other commands run: $0 help"
  COMMAND=dev
else
  COMMAND=$1
  shift || true
fi
shift || true
case "$COMMAND" in
  dev)
    start_dev
    ;;
  docker)
    start_docker
    ;;
  docker-detached)
    start_docker_detached
    ;;
  stop)
    stop_services
    ;;
  seed)
    seed_db
    ;;
  help|--help|-h)
    usage
    ;;
  *)
    echo "Unknown command: $COMMAND"
    usage
    exit 2
    ;;
esac
