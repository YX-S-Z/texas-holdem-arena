"""In-memory game sessions: one GameController per game_id."""

from typing import Dict, List, Optional, Any

from engine.game_state import GameConfig, Player
from engine.game_controller import GameController


# game_id -> (controller, bot_player_ids)
_sessions: Dict[str, tuple] = {}


def create_game(
    game_id: Optional[str] = None,
    num_players: int = 2,
    small_blind: int = 5,
    big_blind: int = 10,
    starting_stack: int = 1000,
    bot_player_ids: Optional[List[str]] = None,
) -> str:
    config = GameConfig(
        small_blind=small_blind,
        big_blind=big_blind,
        starting_stack=starting_stack,
    )
    players = [
        Player(id=f"player_{i}", seat=i, stack=starting_stack, display_name=f"Player {i}")
        for i in range(num_players)
    ]
    controller = GameController(config=config, players=players)
    controller.start_hand()
    gid = game_id or f"game_{len(_sessions)}"
    _sessions[gid] = (controller, bot_player_ids or [])
    return gid


def get_game(game_id: str) -> Optional[GameController]:
    t = _sessions.get(game_id)
    return t[0] if t else None


def get_bot_ids(game_id: str) -> List[str]:
    t = _sessions.get(game_id)
    return list(t[1]) if t else []


def is_bot_turn(game_id: str) -> bool:
    game = get_game(game_id)
    if not game:
        return False
    seat = game._current_player_seat
    if seat is None:
        return False
    p = game._player_by_seat(seat)
    return p is not None and p.id in get_bot_ids(game_id)


def next_hand(game_id: str) -> bool:
    """Start the next hand in the same game. Returns True if started."""
    game = get_game(game_id)
    if not game:
        return False
    game.start_hand()
    return True


def apply_bot_action(game_id: str) -> Optional[Dict[str, Any]]:
    """If current player is a bot, apply a simple action (call/check/fold) and return action taken."""
    game = get_game(game_id)
    if not game:
        return None
    seat = game._current_player_seat
    if seat is None:
        return None
    p = game._player_by_seat(seat)
    if not p or p.id not in get_bot_ids(game_id):
        return None
    actions = game.get_legal_actions(p.id)
    if not actions:
        return None
    # Simple bot: prefer check/call, then call, then fold
    for action in actions:
        if action["type"] == "check":
            game.apply_action(p.id, action)
            return action
        if action["type"] == "call":
            game.apply_action(p.id, action)
            return action
    for action in actions:
        if action["type"] == "fold":
            game.apply_action(p.id, action)
            return action
    return None
