#!/bin/sh
# Starts the read API (private, 127.0.0.1:8000) and the web server (public, $PORT).
# If either stops, the other is stopped too and the machine exits, so Fly restarts it.
set -eu
uvicorn api.app:app --host 127.0.0.1 --port 8000 --no-access-log &
api=$!
node web/server.js &
web=$!
trap 'kill $api $web 2>/dev/null' TERM INT
# Exit as soon as either process ends.
while kill -0 $api 2>/dev/null && kill -0 $web 2>/dev/null; do sleep 2; done
kill $api $web 2>/dev/null || true
exit 1
