"""Shared state for the arena.py launcher session."""

from typing import Any, Dict, Optional

_state: Dict[str, Any] = {
    "game_id": None,
    "spectator": False,
    "finished": False,
    "summary_shown": False,  # set by browser after rendering the leaderboard overlay
}


def get_state() -> Dict[str, Any]:
    return dict(_state)


def set_state(game_id: str, spectator: bool = False) -> None:
    _state["game_id"] = game_id
    _state["spectator"] = spectator
    _state["finished"] = False
    _state["summary_shown"] = False


def set_game_id(game_id: str) -> None:
    _state["game_id"] = game_id
    _state["finished"] = False
    _state["summary_shown"] = False


def set_finished() -> None:
    _state["finished"] = True


def set_summary_shown() -> None:
    _state["summary_shown"] = True
