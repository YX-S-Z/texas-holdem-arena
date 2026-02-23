"""
Persist per-game action logs and hand results to data/ for offline analysis.

Each game gets its own folder named  data/YYYYMMDD_HHMMSS_<game_id>/
so runs never overwrite each other.

Files inside each folder:
  actions.csv  — one row per fold/check/call/raise action
  hands.csv    — one row per completed hand (winners, pot)

Load in pandas:
  import pandas as pd, glob, os
  actions = pd.concat([pd.read_csv(f) for f in glob.glob("data/*/actions.csv")])
  hands   = pd.concat([pd.read_csv(f) for f in glob.glob("data/*/hands.csv")])
"""

import csv
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

_lock = threading.Lock()

# game_id -> absolute path of that game's folder (populated by init_game_log)
_game_dirs: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Column schemas
# ---------------------------------------------------------------------------

ACTION_FIELDS = [
    "timestamp",        # ISO-8601 UTC
    "game_id",
    "hand_number",      # 0-indexed; increments on next_hand
    "phase",            # preflop / flop / turn / river
    "player_id",        # e.g. player_0
    "display_name",     # human-readable model name or "Human"
    "hole_cards",       # space-separated codes, e.g. "HA CT"
    "community_cards",  # space-separated, e.g. "C4 H2 DA"  (empty preflop)
    "pot",              # chips in pot when this action is taken
    "stack",            # player's stack before this action
    "current_bet",      # player's street bet before this action
    "action_type",      # fold / check / call / raise
    "action_amount",    # integer for call/raise; empty for fold/check
    "thinking",         # LLM chain-of-thought (may be multi-line; CSV-quoted)
    "failure_reason",   # timeout / parse_error / api_error; empty = success
    "talk",             # table talk message (bluff mode); empty if silent
]

HAND_FIELDS = [
    "timestamp",
    "game_id",
    "hand_number",
    "winner_player_ids",     # "|"-joined when split pot
    "winner_display_names",  # "|"-joined
    "winner_amounts",        # "|"-joined chip amounts
    "winner_hand_names",     # "|"-joined hand type strings, e.g. "pair"
    "pot",                   # total chips distributed (sum of winner amounts)
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _game_dir(game_id: str) -> str:
    """Return (and cache) the folder path for game_id, creating it if needed."""
    if game_id not in _game_dirs:
        # Fallback: create folder on first use if init_game_log was never called.
        _init(game_id)
    return _game_dirs[game_id]


def _init(game_id: str) -> None:
    """Create the folder for game_id and register it.

    If the environment variable ARENA_GAME_DIR is set, that path is used
    directly (used by batch runs to place each game inside a shared folder).
    Otherwise a timestamped folder is created under data/.
    """
    env_dir = os.environ.get("ARENA_GAME_DIR")
    if env_dir:
        folder = os.path.abspath(env_dir)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(DATA_DIR, f"{ts}_{game_id}")
    os.makedirs(folder, exist_ok=True)
    _game_dirs[game_id] = folder


def _append_row(path: str, fields: List[str], row: Dict[str, Any]) -> None:
    """Append one dict row to a CSV file, writing the header if the file is new."""
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_game_log(game_id: str) -> None:
    """Create the data folder for this game. Call once from create_game()."""
    with _lock:
        if game_id not in _game_dirs:
            _init(game_id)


def get_game_dir(game_id: str) -> Optional[str]:
    """Return the data folder path for game_id, or None if not yet initialised."""
    return _game_dirs.get(game_id)


def log_action(
    *,
    game_id: str,
    hand_number: int,
    phase: str,
    player_id: str,
    display_name: str,
    hole_cards: List[str],
    community_cards: List[str],
    pot: int,
    stack: int,
    current_bet: int,
    action_type: str,
    action_amount: Optional[int],
    thinking: Optional[str],
    failure_reason: Optional[str],
    talk: Optional[str] = None,
) -> None:
    """Append one action row to actions.csv inside this game's folder (thread-safe)."""
    row = {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "game_id":         game_id,
        "hand_number":     hand_number,
        "phase":           phase,
        "player_id":       player_id,
        "display_name":    display_name,
        "hole_cards":      " ".join(hole_cards),
        "community_cards": " ".join(community_cards),
        "pot":             pot,
        "stack":           stack,
        "current_bet":     current_bet,
        "action_type":     action_type,
        "action_amount":   "" if action_amount is None else action_amount,
        "thinking":        thinking or "",
        "failure_reason":  failure_reason or "",
        "talk":            talk or "",
    }
    with _lock:
        path = os.path.join(_game_dir(game_id), "actions.csv")
        _append_row(path, ACTION_FIELDS, row)


def log_hand_result(
    *,
    game_id: str,
    hand_number: int,
    winners: List[Dict[str, Any]],
    pot: int,
) -> None:
    """Append one hand-result row to hands.csv inside this game's folder (thread-safe).

    Each winner dict should contain: player_id, display_name, amount, hand_name.
    """
    row = {
        "timestamp":            datetime.now(timezone.utc).isoformat(),
        "game_id":              game_id,
        "hand_number":          hand_number,
        "winner_player_ids":    "|".join(w.get("player_id",    "")      for w in winners),
        "winner_display_names": "|".join(w.get("display_name", "")      for w in winners),
        "winner_amounts":       "|".join(str(w.get("amount",   0))      for w in winners),
        "winner_hand_names":    "|".join(w.get("hand_name",    "") or "" for w in winners),
        "pot":                  pot,
    }
    with _lock:
        path = os.path.join(_game_dir(game_id), "hands.csv")
        _append_row(path, HAND_FIELDS, row)
