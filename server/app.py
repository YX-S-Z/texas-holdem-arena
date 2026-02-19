"""FastAPI app: serve game API and static web UI."""

import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware import Middleware
from starlette.responses import Response

from engine.game_state import GameConfig, Player
from engine.game_controller import GameController
import data_logger
from .game_session import (
    create_game,
    clone_game,
    get_game,
    get_last_action,
    get_hands_played,
    get_bust_order,
    get_failure_stats,
    is_bot_turn,
    apply_bot_action,
    next_hand,
    finalize_game_log,
)
from .arena_state import (
    get_state as get_arena_state,
    set_state as set_arena_state,
    set_game_id as set_arena_game_id,
    set_finished as set_arena_finished,
    set_summary_shown as set_arena_summary_shown,
)


app = FastAPI(title="Texas Hold'em Arena")

# Static files (mounted after routes so /games/ isn't shadowed)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


class CreateGameBody(BaseModel):
    num_players: int = 2
    small_blind: int = 5
    big_blind: int = 10
    starting_stack: int = 1000
    bot_player_ids: Optional[list] = None
    player_models: Optional[dict] = None  # {player_id: model_alias}
    player_names: Optional[dict] = None   # {player_id: display_name}


class ActionBody(BaseModel):
    player_id: str
    action: dict


@app.post("/games")
def api_create_game(body: CreateGameBody):
    """Create a new game. Default: 2 players, player_1 is bot."""
    bot_ids = body.bot_player_ids if body.bot_player_ids is not None else ["player_1"]
    game_id = create_game(
        num_players=body.num_players,
        small_blind=body.small_blind,
        big_blind=body.big_blind,
        starting_stack=body.starting_stack,
        bot_player_ids=bot_ids,
        player_models=body.player_models,
        player_names=body.player_names,
    )
    return {"game_id": game_id}


@app.get("/games/{game_id}")
def api_get_state(game_id: str, viewer_id: Optional[str] = None):
    """Get game state. Pass viewer_id to see only that player's hole cards (e.g. viewer_id=player_0)."""
    game = get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    state = game.get_state(viewer_id=viewer_id)
    state["last_action"] = get_last_action(game_id)
    state["hands_played"] = get_hands_played(game_id)
    state["bust_order"] = get_bust_order(game_id)
    state["failure_stats"] = get_failure_stats(game_id)
    state["arena_finished"] = get_arena_state().get("finished", False)
    return state


@app.post("/games/{game_id}/action")
def api_apply_action(game_id: str, body: ActionBody):
    """Submit an action (fold, check, call, raise) for a player."""
    game = get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    # Capture pre-action state for logging before the action mutates game state.
    pre_state = game.get_state(viewer_id=body.player_id)
    try:
        game.apply_action(body.player_id, body.action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Log the human player's action.
    _p_pre = next(
        (ps for ps in pre_state.get("players", []) if ps["id"] == body.player_id), {}
    )
    data_logger.log_action(
        game_id=game_id,
        hand_number=get_hands_played(game_id),
        phase=pre_state.get("phase", ""),
        player_id=body.player_id,
        display_name=_p_pre.get("display_name", body.player_id),
        hole_cards=_p_pre.get("hole_cards") or [],
        community_cards=pre_state.get("community_cards") or [],
        pot=pre_state.get("pot", 0),
        stack=_p_pre.get("stack", 0),
        current_bet=_p_pre.get("current_bet", 0),
        action_type=body.action.get("type", "?"),
        action_amount=body.action.get("amount"),
        thinking=None,
        failure_reason=None,
    )
    state = game.get_state(viewer_id=body.player_id)
    state["last_action"] = get_last_action(game_id)
    state["hands_played"] = get_hands_played(game_id)
    state["bust_order"] = get_bust_order(game_id)
    state["failure_stats"] = get_failure_stats(game_id)
    state["arena_finished"] = get_arena_state().get("finished", False)
    return state


@app.post("/games/{game_id}/bot_move")
def api_bot_move(game_id: str, viewer_id: Optional[str] = None):
    """If current player is a bot, apply one action and return new state for viewer_id."""
    game = get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    result = apply_bot_action(game_id)
    state = game.get_state(viewer_id=viewer_id or "player_0")
    state["last_action"] = get_last_action(game_id)
    state["hands_played"] = get_hands_played(game_id)
    state["bust_order"] = get_bust_order(game_id)
    state["failure_stats"] = get_failure_stats(game_id)
    state["arena_finished"] = get_arena_state().get("finished", False)
    # True when an action was actually applied; False when the lock was contended
    # (another call is in-flight) or no bot turn is pending.  arena.py uses this
    # to skip screenshots when the game state has not actually advanced.
    state["action_applied"] = result is not None
    return state


@app.get("/games/{game_id}/is_bot_turn")
def api_is_bot_turn(game_id: str):
    return {"is_bot_turn": is_bot_turn(game_id)}


@app.post("/games/{game_id}/next_hand")
def api_next_hand(game_id: str, viewer_id: Optional[str] = None):
    """Start the next hand (after hand_over). Returns new state."""
    game = get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if not next_hand(game_id):
        raise HTTPException(status_code=400, detail="Could not start next hand")
    state = game.get_state(viewer_id=viewer_id or "player_0")
    state["last_action"] = get_last_action(game_id)
    state["hands_played"] = get_hands_played(game_id)
    state["bust_order"] = get_bust_order(game_id)
    state["failure_stats"] = get_failure_stats(game_id)
    state["arena_finished"] = get_arena_state().get("finished", False)
    return state


class ArenaRegisterBody(BaseModel):
    game_id: str
    spectator: bool = False


@app.post("/arena/register")
def arena_register(body: ArenaRegisterBody):
    """Called by arena.py on startup to register the active session."""
    set_arena_state(body.game_id, body.spectator)
    return {"ok": True}


@app.get("/arena/status")
def arena_status():
    """Returns current arena state: {game_id, spectator}."""
    return get_arena_state()


@app.post("/arena/finish")
def arena_finish():
    """Called by arena.py when all hands are done. Signals the browser to show the summary."""
    # Flush the final hand result (spectator mode: arena.py never calls next_hand after the last hand).
    arena_state = get_arena_state()
    if arena_state.get("game_id"):
        finalize_game_log(arena_state["game_id"])
    set_arena_finished()
    return {"ok": True}


@app.post("/arena/ack_summary")
def arena_ack_summary():
    """Called by the browser after it successfully renders the game-over leaderboard.
    arena.py polls /arena/status for summary_shown=True before exiting."""
    # Flush the final hand result (human mode: next_hand is never called after the last hand).
    arena_state = get_arena_state()
    if arena_state.get("game_id"):
        finalize_game_log(arena_state["game_id"])
    set_arena_summary_shown()
    return {"ok": True}


@app.post("/arena/restart")
def arena_restart_game():
    """Clone the current arena game and return the new game_id.
    Used by the browser 'Restart' button; arena.py polls /arena/status to detect the switch."""
    state = get_arena_state()
    if not state["game_id"]:
        raise HTTPException(status_code=400, detail="No arena session active")
    new_id = clone_game(state["game_id"])
    if not new_id:
        raise HTTPException(status_code=500, detail="Failed to clone game")
    set_arena_game_id(new_id)
    return {"game_id": new_id, "spectator": state["spectator"]}


@app.get("/")
def index():
    resp = FileResponse(os.path.join(STATIC_DIR, "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/static/{path:path}")
def static_file(path: str):
    """Serve static files with no-cache headers during development."""
    full = os.path.join(STATIC_DIR, path)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Not found")
    resp = FileResponse(full)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp
