#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Texas Hold'em LLM Arena — Analysis Runner
#
# Usage:
#   ./analysis/run_analysis.sh                          # analyse ALL games
#   ./analysis/run_analysis.sh data/20260218_225446_game_0  # one specific game
#   ./analysis/run_analysis.sh latest                   # most recently modified game
#
# Output is written next to the data:
#   <game-folder>/report.md, metrics.csv, *.png
# Or, for the all-games mode:
#   analysis/output/report.md, metrics.csv, *.png
#
# Requirements (install once):
#   pip install pandas numpy matplotlib
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Resolve script location so the script works from any working directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

cd "$REPO_DIR"

# ── Parse argument ────────────────────────────────────────────────────────────
GAME_DIR=""
if [[ "${1:-}" == "latest" ]]; then
    GAME_DIR=$(ls -td data/*/ 2>/dev/null | head -1)
    if [[ -z "$GAME_DIR" ]]; then
        echo "Error: no game folders found under data/" >&2
        exit 1
    fi
    echo "► Using latest game folder: $GAME_DIR"
elif [[ -n "${1:-}" ]]; then
    GAME_DIR="$1"
fi

# ── Check dependencies ────────────────────────────────────────────────────────
python - <<'PYCHECK'
import sys
missing = []
for pkg in ("pandas", "numpy", "matplotlib"):
    try:
        __import__(pkg)
    except ImportError:
        missing.append(pkg)
if missing:
    print(f"Missing packages: {', '.join(missing)}")
    print(f"Install with:  pip install {' '.join(missing)}")
    sys.exit(1)
PYCHECK

# ── Run pipeline ──────────────────────────────────────────────────────────────
if [[ -n "$GAME_DIR" ]]; then
    python analysis/poker_analysis.py --game-dir "$GAME_DIR"
else
    python analysis/poker_analysis.py
fi
