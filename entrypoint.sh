#!/bin/bash
# Supervise uvicorn (backend) + nginx (frontend) in one container.
# If either dies, the container exits — letting Docker restart it.
set -e

shutdown() {
    echo "Received signal, shutting down..."
    kill -TERM "$UVICORN_PID" "$NGINX_PID" 2>/dev/null || true
    wait
    exit 0
}
trap shutdown TERM INT

# Backend bound to localhost — only nginx talks to it
uvicorn app.main:app --host 127.0.0.1 --port 8001 --log-level info &
UVICORN_PID=$!

nginx -g 'daemon off;' &
NGINX_PID=$!

# Exit as soon as one of them dies
wait -n
EXIT_CODE=$?
echo "A child process exited with code $EXIT_CODE, shutting down container" >&2
kill -TERM "$UVICORN_PID" "$NGINX_PID" 2>/dev/null || true
wait
exit "$EXIT_CODE"
