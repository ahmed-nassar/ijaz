#!/bin/sh
# Starts the read API (private, 127.0.0.1:8000) and the web server (public, $PORT).
# The web server starts only once the API answers, so the first visit after a cold
# start never reaches a closed port. If either process stops, the other is stopped
# too and the machine exits, so Fly restarts it.
set -eu
uvicorn api.app:app --host 127.0.0.1 --port 8000 --no-access-log &
api=$!
stopping=0
trap 'stopping=1; kill $api ${web:-} 2>/dev/null || true' TERM INT

# Wait up to 30 seconds for /api/health (one Python process polls every 0.2 s).
python - "$api" <<'PY' || { echo "start-web: API did not start" >&2; kill $api 2>/dev/null || true; exit 1; }
import os, sys, time, urllib.request
pid = int(sys.argv[1])
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        os.kill(pid, 0)
    except OSError:
        sys.exit(1)  # uvicorn exited
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=1):
            sys.exit(0)
    except OSError:
        time.sleep(0.2)
sys.exit(1)
PY

node web/server.js &
web=$!
# Exit as soon as either process ends.
while [ $stopping = 0 ] && kill -0 $api 2>/dev/null && kill -0 $web 2>/dev/null; do sleep 2; done
kill $api $web 2>/dev/null || true
[ $stopping = 1 ] && exit 0
exit 1
