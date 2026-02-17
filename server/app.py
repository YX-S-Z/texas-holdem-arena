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
from .game_session import (
    create_game,
    get_game,
    is_bot_turn,
    apply_bot_action,
    next_hand,
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
    )
    return {"game_id": game_id}


@app.get("/games/{game_id}")
def api_get_state(game_id: str, viewer_id: Optional[str] = None):
    """Get game state. Pass viewer_id to see only that player's hole cards (e.g. viewer_id=player_0)."""
    game = get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game.get_state(viewer_id=viewer_id)


@app.post("/games/{game_id}/action")
def api_apply_action(game_id: str, body: ActionBody):
    """Submit an action (fold, check, call, raise) for a player."""
    game = get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    try:
        game.apply_action(body.player_id, body.action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return game.get_state(viewer_id=body.player_id)


@app.post("/games/{game_id}/bot_move")
def api_bot_move(game_id: str, viewer_id: Optional[str] = None):
    """If current player is a bot, apply one action and return new state for viewer_id."""
    game = get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    applied = apply_bot_action(game_id)
    return game.get_state(viewer_id=viewer_id or "player_0")


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
    return game.get_state(viewer_id=viewer_id or "player_0")


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
