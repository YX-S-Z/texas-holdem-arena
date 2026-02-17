"""In-memory game sessions: one GameController per game_id."""

import os
from typing import Any, Dict, List, Optional

from engine.game_state import GameConfig, Player
from engine.game_controller import GameController
from bots.openrouter_bot import OpenRouterBot, model_display_name, resolve_model


# game_id -> (controller, bots)
# bots: dict[player_id -> OpenRouterBot | None]
#   OpenRouterBot  → LLM-backed bot
#   None           → simple rule-based bot (legacy / "New game" button)
#   key absent     → human player
_sessions: Dict[str, tuple] = {}


def create_game(
    game_id: Optional[str] = None,
    num_players: int = 2,
    small_blind: int = 5,
    big_blind: int = 10,
    starting_stack: int = 1000,
    bot_player_ids: Optional[List[str]] = None,
    player_models: Optional[Dict[str, str]] = None,
) -> str:
    """
    Create and start a new game.

    player_models: maps player_id -> model alias or full model ID.
                   When provided, those players become LLM bots.
                   Any player_id not in player_models and not in
                   bot_player_ids is treated as a human.
    bot_player_ids: legacy simple-bot list (used by the browser's New Game button).
    """
    config = GameConfig(
        small_blind=small_blind,
        big_blind=big_blind,
        starting_stack=starting_stack,
    )

    # Build display names: LLM bots get a nice name, others get "Player N"
    def _display_name(i: int) -> str:
        pid = f"player_{i}"
        if player_models and pid in player_models:
            return model_display_name(resolve_model(player_models[pid]))
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

    # Build bots dict
    api_key = os.environ.get("API_KEY", "")
    bots: Dict[str, Any] = {}

    if player_models:
        for pid, model_alias in player_models.items():
            bots[pid] = OpenRouterBot(api_key=api_key, model=model_alias)
    elif bot_player_ids:
        # Legacy simple bots (None = rule-based)
        for pid in bot_player_ids:
            bots[pid] = None

    _sessions[gid] = (controller, bots)
    return gid


def get_game(game_id: str) -> Optional[GameController]:
    t = _sessions.get(game_id)
    return t[0] if t else None


def get_bots(game_id: str) -> Dict[str, Any]:
    t = _sessions.get(game_id)
    return dict(t[1]) if t else {}


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
    """Start the next hand in the same game. Returns True if started."""
    game = get_game(game_id)
    if not game:
        return False
    game.start_hand()
    return True


def apply_bot_action(game_id: str) -> Optional[Dict[str, Any]]:
    """
    If the current player is a bot, apply one action and return it.
    - OpenRouterBot: asks the LLM
    - None (simple bot): prefer check/call, then fold
    """
    game = get_game(game_id)
    if not game:
        return None
    seat = game._current_player_seat
    if seat is None:
        return None
    p = game._player_by_seat(seat)
    if not p:
        return None

    bots = get_bots(game_id)
    if p.id not in bots:
        return None  # human player

    legal = game.get_legal_actions(p.id)
    if not legal:
        return None

    bot = bots[p.id]

    if isinstance(bot, OpenRouterBot):
        # Get the state from this bot's perspective and ask the LLM
        state = game.get_state(viewer_id=p.id)
        action = bot.decide(state, p.id)
    else:
        # Simple rule-based fallback (None bot)
        action = _simple_action(legal)

    game.apply_action(p.id, action)
    return action


def _simple_action(legal: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Prefer check > call > fold."""
    types = {a["type"]: a for a in legal}
    if "check" in types:
        return {"type": "check"}
    if "call" in types:
        return {"type": "call", "amount": types["call"]["amount"]}
    return {"type": "fold"}
