#!/usr/bin/env bash
# Run the Texas Hold'em Arena server and open the game in your browser.
# Usage: ./run.sh   (from the texas_holdem_arena directory)

set -e
cd "$(dirname "$0")"

echo "Installing dependencies (if needed)..."
pip install -q -r requirements.txt 2>/dev/null || true

echo "Starting server at http://127.0.0.1:8000"
echo "Open that URL in your browser and click 'New game' to play."
echo ""

PYTHONPATH=. exec uvicorn server.app:app --host 0.0.0.0 --port 8000
