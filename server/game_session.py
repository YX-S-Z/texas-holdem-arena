"""In-memory game sessions: one GameController per game_id."""

import os
import threading
from typing import Any, Dict, List, Optional

from engine.game_state import GameConfig, Player
from engine.game_controller import GameController
from bots import create_bot
from bots.random_bot import RandomBot
from bots.openrouter_bot import OpenRouterBot, model_display_name, resolve_model
import data_logger


# game_id -> {"controller": ..., "bots": ..., "last_action": ...}
_sessions: Dict[str, Dict[str, Any]] = {}

# Per-game locks to prevent concurrent bot_move calls on the same game
_game_locks: Dict[str, threading.Lock] = {}
_game_locks_mutex = threading.Lock()


def _get_game_lock(game_id: str) -> threading.Lock:
    with _game_locks_mutex:
        if game_id not in _game_locks:
            _game_locks[game_id] = threading.Lock()
        return _game_locks[game_id]


def create_game(
    game_id: Optional[str] = None,
    num_players: int = 2,
    small_blind: int = 5,
    big_blind: int = 10,
    starting_stack: int = 1000,
    bot_player_ids: Optional[List[str]] = None,
    player_models: Optional[Dict[str, str]] = None,
    player_names: Optional[Dict[str, str]] = None,
    bluff_mode: bool = False,
) -> str:
    config = GameConfig(
        small_blind=small_blind,
        big_blind=big_blind,
        starting_stack=starting_stack,
    )

    def _display_name(i: int) -> str:
        pid = f"player_{i}"
        if player_names and pid in player_names:
            return player_names[pid]
        if player_models and pid in player_models:
            spec = player_models[pid]
            if spec == "random":
                return "Random Bot"
            if spec == "simple":
                return f"Player {i}"
            return model_display_name(resolve_model(spec))
        return f"Player {i}"

    players = [
        Player(
            id=f"player_{i}",
            seat=i,
            stack=starting_stack,
            display_name=_display_name(i),
        )
        for i in range(num_players)
    ]

    controller = GameController(config=config, players=players)
    controller.start_hand()

    gid = game_id or f"game_{len(_sessions)}"

    api_key = os.environ.get("API_KEY", "")
    bots: Dict[str, Any] = {}

    if player_models:
        for pid, spec in player_models.items():
            bots[pid] = create_bot(spec, api_key=api_key, bluff_mode=bluff_mode)
    elif bot_player_ids:
        for pid in bot_player_ids:
            bots[pid] = None

    data_logger.init_game_log(gid)

    _sessions[gid] = {
        "controller": controller,
        "bots": bots,
        "last_action": None,        # populated by apply_bot_action
        "hands_played": 0,          # number of completed hands (incremented on next_hand)
        "hand_result_logged": False, # True once the current hand's result is written to disk
        "bust_order": [],           # [{player_id, display_name, hand_number}] in bust order
        "failure_stats": {},        # {player_id: {total_moves, timeout, parse_error, api_error}}
        "hand_talks": [],           # [{player_id, display_name, talk}] — reset each hand
        "creation_config": {   # stored so the game can be cloned
            "num_players": num_players,
            "small_blind": small_blind,
            "big_blind": big_blind,
            "starting_stack": starting_stack,
            "bot_player_ids": bot_player_ids,
            "player_models": player_models,
            "player_names": player_names,
            "bluff_mode": bluff_mode,
        },
    }
    return gid


def get_game(game_id: str) -> Optional[GameController]:
    s = _sessions.get(game_id)
    return s["controller"] if s else None


def get_bots(game_id: str) -> Dict[str, Any]:
    s = _sessions.get(game_id)
    return dict(s["bots"]) if s else {}


def get_last_action(game_id: str) -> Optional[Dict[str, Any]]:
    s = _sessions.get(game_id)
    return s["last_action"] if s else None


def set_last_action(game_id: str, action: Dict[str, Any]) -> None:
    s = _sessions.get(game_id)
    if s:
        s["last_action"] = action


def _update_bust_order(session: Dict[str, Any]) -> None:
    """Check for any newly 0-chip players and record them in bust_order."""
    bust_order = session["bust_order"]
    already_busted = {entry["player_id"] for entry in bust_order}
    hand_num = session["hands_played"]
    # Use the public get_state() API to read player stacks
    state = session["controller"].get_state()
    for p in state.get("players", []):
        if p["stack"] == 0 and p["id"] not in already_busted:
            bust_order.append({
                "player_id": p["id"],
                "display_name": p.get("display_name", p["id"]),
                "hand_number": hand_num,
            })


def get_hands_played(game_id: str) -> int:
    s = _sessions.get(game_id)
    return s["hands_played"] if s else 0


def get_bust_order(game_id: str) -> List[Dict[str, Any]]:
    """Return bust_order, lazily updating it first to catch the latest busts."""
    s = _sessions.get(game_id)
    if not s:
        return []
    _update_bust_order(s)
    return list(s["bust_order"])


def _record_move(session: Dict[str, Any], player_id: str,
                 failure_reason: Optional[str] = None) -> None:
    """Increment total_moves (and the failure counter if applicable) for a player."""
    stats = session["failure_stats"]
    if player_id not in stats:
        stats[player_id] = {
            "total_moves": 0,
            "timeout": 0,
            "parse_error": 0,           # both primary + guardrail failed → fallback used
            "parse_error_rescued": 0,   # primary failed but guardrail saved the action
            "api_error": 0,
        }
    stats[player_id]["total_moves"] += 1
    if failure_reason and failure_reason in stats[player_id]:
        stats[player_id][failure_reason] += 1


def get_failure_stats(game_id: str) -> Dict[str, Any]:
    s = _sessions.get(game_id)
    return dict(s["failure_stats"]) if s else {}


def get_hand_talks(game_id: str) -> List[Dict[str, Any]]:
    """Return the list of table talk messages for the current hand."""
    s = _sessions.get(game_id)
    return list(s["hand_talks"]) if s else []


def add_hand_talk(game_id: str, player_id: str, display_name: str, talk: str) -> None:
    """Append a talk message to the current hand's talk list."""
    s = _sessions.get(game_id)
    if s and talk:
        s["hand_talks"].append({
            "player_id": player_id,
            "display_name": display_name,
            "talk": talk,
        })


def get_bluff_mode(game_id: str) -> bool:
    """Return whether bluff mode is enabled for this game."""
    s = _sessions.get(game_id)
    if not s:
        return False
    return s.get("creation_config", {}).get("bluff_mode", False)


def is_bot_turn(game_id: str) -> bool:
    game = get_game(game_id)
    if not game:
        return False
    seat = game._current_player_seat
    if seat is None:
        return False
    p = game._player_by_seat(seat)
    return p is not None and p.id in get_bots(game_id)


def next_hand(game_id: str) -> bool:
    """Start the next hand. Updates bust tracking and increments hands_played.

    Returns False (without starting a hand) when fewer than 2 players have chips —
    indicating natural game-over; the caller should show the leaderboard instead.
    """
    s = _sessions.get(game_id)
    if not s:
        return False
    _log_current_hand_result(game_id, s)   # write hand result before resetting state
    _update_bust_order(s)                  # capture any busts before starting next hand
    # Guard: refuse to start a hand if fewer than 2 players can still play
    active = [p for p in s["controller"].players if p.stack > 0]
    if len(active) < 2:
        return False
    s["hands_played"] += 1
    s["hand_result_logged"] = False        # ready to log the new hand
    s["hand_talks"] = []                   # reset table talk for new hand
    s["controller"].start_hand()
    s["last_action"] = None
    return True


def finalize_game_log(game_id: str) -> None:
    """Log the final hand result when the game ends without a subsequent next_hand call.

    Called from arena_finish (spectator mode) and arena_ack_summary (human mode)
    so the very last hand is always captured.
    """
    s = _sessions.get(game_id)
    if not s:
        return
    _log_current_hand_result(game_id, s)


def _log_current_hand_result(game_id: str, s: Dict[str, Any]) -> None:
    """Write the current hand's result to disk (idempotent via hand_result_logged flag)."""
    if s.get("hand_result_logged"):
        return
    state = s["controller"].get_state()
    winners = state.get("winners") or []
    if not winners:
        return
    name_map = {p.id: (p.display_name or p.id) for p in s["controller"].players}
    enriched = [
        {**w, "display_name": name_map.get(w["player_id"], w["player_id"])}
        for w in winners
    ]
    pot = sum(w.get("amount", 0) for w in winners)
    data_logger.log_hand_result(
        game_id=game_id,
        hand_number=s["hands_played"],
        winners=enriched,
        pot=pot,
    )
    s["hand_result_logged"] = True


def apply_bot_action(game_id: str) -> Optional[Dict[str, Any]]:
    """
    If the current player is a bot, apply one action and return a summary dict:
      {"player_id", "display_name", "action", "thinking"}
    Returns None if the current player is not a bot or if another bot_move is
    already in flight for this game (prevents the 'Cannot act now' race condition).
    """
    s = _sessions.get(game_id)
    if not s:
        return None

    # Non-blocking acquire: if another thread is already processing a bot move
    # for this game, skip rather than double-apply.
    lock = _get_game_lock(game_id)
    if not lock.acquire(blocking=False):
        return None

    try:
        game: GameController = s["controller"]

        seat = game._current_player_seat
        if seat is None:
            return None
        p = game._player_by_seat(seat)
        if not p:
            return None

        bots = s["bots"]
        if p.id not in bots:
            return None  # human player

        legal = game.get_legal_actions(p.id)
        if not legal:
            return None

        bot = bots[p.id]
        thinking: Optional[str] = None
        failure_reason: Optional[str] = None
        raw_response: Optional[str] = None

        # Fetch state once before the action (used for bot decision and logging).
        pre_state = game.get_state(viewer_id=p.id)

        talk: Optional[str] = None

        if isinstance(bot, OpenRouterBot):
            action = bot.decide(pre_state, p.id, talk_history=s["hand_talks"])
            thinking = getattr(bot, "last_thinking", None)
            failure_reason = getattr(bot, "last_failure_reason", None)
            raw_response = getattr(bot, "last_raw_response", None)
            talk = getattr(bot, "last_talk", None)
        elif isinstance(bot, RandomBot):
            action = bot.decide(pre_state, p.id)
        else:
            action = _simple_action(legal)

        try:
            game.apply_action(p.id, action)
        except ValueError:
            # Race condition: game state changed while the LLM was thinking
            # (e.g. arena.py timed out and retried; the first call finally arrived).
            return None

        _record_move(s, p.id, failure_reason)

        # Log this action to disk.
        _player_pre = next(
            (ps for ps in pre_state.get("players", []) if ps["id"] == p.id), {}
        )
        data_logger.log_action(
            game_id=game_id,
            hand_number=s["hands_played"],
            phase=pre_state.get("phase", ""),
            player_id=p.id,
            display_name=p.display_name or p.id,
            hole_cards=_player_pre.get("hole_cards") or [],
            community_cards=pre_state.get("community_cards") or [],
            pot=pre_state.get("pot", 0),
            stack=_player_pre.get("stack", 0),
            current_bet=_player_pre.get("current_bet", 0),
            action_type=action.get("type", "?"),
            action_amount=action.get("amount"),
            thinking=thinking,
            failure_reason=failure_reason,
            talk=talk,
        )

        # Record table talk for this hand (if any)
        if talk:
            s["hand_talks"].append({
                "player_id": p.id,
                "display_name": p.display_name or p.id,
                "talk": talk,
            })

        # Format a human-readable action label
        atype = action.get("type", "?")
        pre_stack = _player_pre.get("stack", 0)
        if (atype == "raise" or atype == "call") and action.get("amount") == pre_stack and pre_stack > 0:
            action_label = "ALL IN"
        elif atype == "raise":
            action_label = f"raise to {action.get('amount')}"
        elif atype == "call":
            action_label = f"call {action.get('amount')}"
        else:
            action_label = atype

        summary = {
            "player_id": p.id,
            "display_name": p.display_name or p.id,
            "action_label": action_label,
            "thinking": thinking,
            "talk": talk,
            "failure_reason": failure_reason,  # None = success; else "timeout"/"parse_error"/"api_error"
            "raw_response": raw_response if failure_reason else None,  # only include on failures to save bandwidth
        }
        s["last_action"] = summary
        return summary
    finally:
        lock.release()


def clone_game(game_id: str) -> Optional[str]:
    """Create a new game with the same configuration as an existing game."""
    s = _sessions.get(game_id)
    if not s:
        return None
    cfg = s.get("creation_config")
    if not cfg:
        return None
    return create_game(**cfg)


def _simple_action(legal: List[Dict[str, Any]]) -> Dict[str, Any]:
    types = {a["type"]: a for a in legal}
    if "check" in types:
        return {"type": "check"}
    if "call" in types:
        return {"type": "call", "amount": types["call"]["amount"]}
    return {"type": "fold"}
