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
  # Run in foreground for logs
  .venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
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
  # If a uvicorn process was started via dev mode, user can stop it with Ctrl+C; not attempting to kill anything automatically here.
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
