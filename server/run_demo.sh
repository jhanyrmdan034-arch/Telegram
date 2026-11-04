#!/usr/bin/env bash
# Starts the custom MTProto backend (server/mtserver) and runs the end-to-end
# test client against it. Both speak the same MTProto 2.0 protocol the patched
# Android app in this repo will use.
#
# Usage:
#   python3 -m venv venv && ./venv/bin/pip install -r server/requirements.txt
#   PYTHON=venv/bin/python bash server/run_demo.sh [port]
set -u
cd "$(dirname "$0")"
PORT="${1:-8443}"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x ../venv/bin/python ]; then PY="../venv/bin/python"; else PY="python3"; fi
fi
echo "using python: $PY  (port $PORT)"
"$PY" -m mtserver.main --listen-host 127.0.0.1 --listen-port "$PORT" \
    --public-host 127.0.0.1 --public-port "$PORT" --dc-id 1 --dev-code 12345 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 2
"$PY" test_client.py --host 127.0.0.1 --port "$PORT" --code 12345
