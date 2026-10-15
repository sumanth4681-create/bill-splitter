#!/usr/bin/env bash
# Start the screen behind a public ngrok tunnel so friends can open share links from anywhere.
# Usage (from bill-splitter/, backend already running on :8000):  scripts/share.sh
# Stop with Ctrl-C; the tunnel closes with it. Needs ngrok (brew install ngrok) with an account configured.
set -euo pipefail
cd "$(dirname "$0")/.."
ngrok http 8501 --log=stdout > /tmp/bill-splitter-ngrok.log 2>&1 &
NGROK=$!
trap 'kill $NGROK 2>/dev/null' EXIT
for _ in $(seq 1 20); do
  URL=$(curl -s localhost:4040/api/tunnels | .venv/bin/python -c "import json,sys; t=json.load(sys.stdin)['tunnels']; print(next(x['public_url'] for x in t if x['public_url'].startswith('https')))" 2>/dev/null || true)
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "${URL:-}" ] || { echo "ngrok did not start; see /tmp/bill-splitter-ngrok.log"; exit 1; }
echo "Public address: $URL   (share links and QR codes will use it)"
APP_URL="$URL" exec .venv/bin/streamlit run frontend/app.py
