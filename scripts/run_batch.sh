#!/usr/bin/env bash
# Run N games sequentially (headless, auto-exit), then analyse all results.
# Each game saves its data to data/TIMESTAMP_game_0/ automatically.
# Screenshots (for video) are saved to data/TIMESTAMP_game_0/game_states_figs/
#
# Usage:
#   bash scripts/run_batch.sh            # runs 5 games with defaults below
#   N_GAMES=3 bash scripts/run_batch.sh  # override number of games

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
API_KEY="${API_KEY:-REDACTED_API_KEY}"
PLAYERS="claude gpt gemini qwen-3.5 kimi minimax glm-5 deepseek grok-fast llama-4"
HANDS="${HANDS:-25}"
N_GAMES="${N_GAMES:-5}"
BASE_PORT="${BASE_PORT:-8010}"   # each game uses BASE_PORT + game_index
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=============================================="
echo "  Texas Hold'em Arena — Batch Run"
echo "  Games   : $N_GAMES"
echo "  Hands   : $HANDS per game"
echo "  Players : $PLAYERS"
echo "=============================================="

# Track data folders created during this batch so we can report them at the end.
GAME_DIRS=()

for i in $(seq 1 "$N_GAMES"); do
    PORT=$(( BASE_PORT + i ))
    echo ""
    echo "══════════════════════════════════════════════"
    echo "  Game $i / $N_GAMES   (port $PORT)"
    echo "══════════════════════════════════════════════"

    # Snapshot existing data folders before this run so we can identify the new one.
    BEFORE=$(ls -d "$ROOT_DIR"/data/*/ 2>/dev/null || true)

    API_KEY="$API_KEY" python "$ROOT_DIR/arena.py" \
        --players $PLAYERS \
        --hands "$HANDS" \
        --port "$PORT" \
        --screenshots \
        --auto-exit \
        --no-browser

    # Find the folder that was just created.
    AFTER=$(ls -d "$ROOT_DIR"/data/*/ 2>/dev/null || true)
    NEW_DIR=$(comm -13 <(echo "$BEFORE" | sort) <(echo "$AFTER" | sort) | head -1)
    if [[ -n "$NEW_DIR" ]]; then
        GAME_DIRS+=("$NEW_DIR")
        echo "  → Data saved to: $NEW_DIR"
    fi

    echo "  Game $i complete."
done

echo ""
echo "══════════════════════════════════════════════"
echo "  All $N_GAMES games complete."
echo "══════════════════════════════════════════════"
echo ""
echo "Game data folders:"
for d in "${GAME_DIRS[@]}"; do
    echo "  $d"
done

echo ""
echo "Running combined analysis across all games..."
bash "$SCRIPT_DIR/run_analysis.sh"

echo ""
echo "Batch complete!"
echo "  - CSV logs    : data/*/"
echo "  - Screenshots : data/*/game_states_figs/"
echo "  - Analysis    : analysis/output/"
