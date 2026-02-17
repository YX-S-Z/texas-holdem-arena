#!/usr/bin/env python3
"""
Texas Hold'em Arena launcher.

Two modes:
  Human vs. models   — one player is "human", the rest are LLMs
  Spectator (all AI) — every seat is an LLM; arena.py drives all moves

Usage examples:
  python arena.py --key sk-or-... --players human claude gpt-4o
  python arena.py --key sk-or-... --players claude gpt-4o gemini llama
  python arena.py --players human claude          # key from env API_KEY
  python arena.py --players claude gpt-4o --hands 10
"""

import argparse
import os
import sys
import time
import threading
import webbrowser

import requests

from bots.openrouter_bot import MODEL_ALIASES, resolve_model

SERVER_HOST = "127.0.0.1"
POLL_INTERVAL = 0.5   # seconds between spectator loop ticks
STARTUP_TIMEOUT = 10  # seconds to wait for the server to start


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

def _start_server(port: int) -> None:
    """Run uvicorn in this thread (called from a daemon thread)."""
    import uvicorn
    uvicorn.run(
        "server.app:app",
        host=SERVER_HOST,
        port=port,
        log_level="warning",
    )


def _wait_for_server(port: int, timeout: int = STARTUP_TIMEOUT) -> bool:
    """Block until the server is accepting connections, or timeout."""
    url = f"http://{SERVER_HOST}:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1)
            if r.status_code < 500:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------------------
# Game management helpers
# ---------------------------------------------------------------------------

def _create_game(base: str, args: argparse.Namespace, player_models: dict) -> str:
    body = {
        "num_players": len(args.players),
        "small_blind": args.small_blind,
        "big_blind": args.big_blind,
        "starting_stack": args.starting_stack,
        "player_models": player_models,
    }
    r = requests.post(f"{base}/games", json=body, timeout=5)
    r.raise_for_status()
    return r.json()["game_id"]


def _get_state(base: str, game_id: str) -> dict:
    r = requests.get(f"{base}/games/{game_id}", timeout=10)
    r.raise_for_status()
    return r.json()


def _bot_move(base: str, game_id: str) -> None:
    requests.post(f"{base}/games/{game_id}/bot_move", timeout=30)


def _next_hand(base: str, game_id: str) -> None:
    requests.post(f"{base}/games/{game_id}/next_hand", timeout=5)


# ---------------------------------------------------------------------------
# Spectator polling loop
# ---------------------------------------------------------------------------

def _spectator_loop(base: str, game_id: str, max_hands: int) -> None:
    """Drive all bot moves and hand transitions. Runs in the main thread."""
    hands_played = 0
    print(f"\nSpectator mode — watching game {game_id}")
    if max_hands:
        print(f"Will stop after {max_hands} hands. Press Ctrl+C to stop early.\n")
    else:
        print("Press Ctrl+C to stop.\n")

    last_phase = None
    last_current = None

    try:
        while True:
            try:
                state = _get_state(base, game_id)
            except requests.RequestException as e:
                print(f"[arena] state fetch error: {e}")
                time.sleep(POLL_INTERVAL)
                continue

            phase = state.get("phase")
            current = state.get("current_player_id")

            # Log phase transitions
            if phase != last_phase or current != last_current:
                if phase in ("hand_over", "showdown"):
                    winners = state.get("winners") or []
                    w_str = ", ".join(
                        f"{w['player_id']} (+{w['amount']})" for w in winners
                    )
                    print(f"  [{phase}] {w_str}")
                elif current:
                    community = " ".join(state.get("community_cards") or []) or "—"
                    print(f"  [{phase}] community={community}  turn={current}")
                last_phase = phase
                last_current = current

            if phase in ("hand_over", "showdown"):
                hands_played += 1
                if max_hands and hands_played >= max_hands:
                    print(f"\nFinished {hands_played} hand(s). Exiting.")
                    sys.exit(0)
                time.sleep(1.0)
                _next_hand(base, game_id)
                last_phase = None
                last_current = None
            elif current is not None:
                _bot_move(base, game_id)
                time.sleep(POLL_INTERVAL)
            else:
                time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(0)


# ---------------------------------------------------------------------------
# Argument parsing and main
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    known_aliases = sorted(MODEL_ALIASES.keys())
    parser = argparse.ArgumentParser(
        description="Texas Hold'em Arena — play against (or watch) LLM bots.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Players:
  "human"   — controlled by you in the browser (always player_0)
  "random"  — picks a random legal action (no API key needed)
  "simple"  — always checks/calls, never raises (no API key needed)
  model     — LLM via OpenRouter; one of: {', '.join(known_aliases)}
              or any full OpenRouter model ID (e.g. anthropic/claude-sonnet-4-6)

Examples (no API key):
  python arena.py --players human random random
  python arena.py --players random random random random --hands 20

Examples (with API key):
  python arena.py --key sk-or-... --players human claude gpt-4o
  python arena.py --key sk-or-... --players claude gpt-4o gemini llama
  python arena.py --key sk-or-... --players human claude --big-blind 20
""",
    )
    parser.add_argument(
        "--key",
        default=os.environ.get("API_KEY"),
        help="OpenRouter API key (or set env var API_KEY)",
    )
    parser.add_argument(
        "--players",
        nargs="+",
        default=["human", "claude"],
        metavar="PLAYER",
        help="Ordered player list: 'human' or a model alias/ID",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--small-blind", type=int, default=5, dest="small_blind")
    parser.add_argument("--big-blind",   type=int, default=10, dest="big_blind")
    parser.add_argument(
        "--starting-stack", type=int, default=500, dest="starting_stack"
    )
    parser.add_argument(
        "--hands",
        type=int,
        default=0,
        help="Spectator mode: stop after this many hands (0 = unlimited)",
    )
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    players = args.players
    num_players = len(players)
    has_human = "human" in players
    spectator = not has_human

    # --- Validate ---
    if num_players < 2 or num_players > 8:
        parser.error("Need between 2 and 8 players.")

    if has_human and players[0] != "human":
        parser.error('"human" must be the first player (seat 0 = player_0).')

    if players.count("human") > 1:
        parser.error('Only one "human" seat is allowed.')

    llm_players = [p for p in players if p not in ("human", "random", "simple")]
    if not args.key and llm_players:
        parser.error(
            "An API key is required for LLM bots. "
            "Pass --key or set the API_KEY environment variable.\n"
            "To run without a key, use only 'random' or 'simple' bots:\n"
            "  python arena.py --players random random random"
        )

    # --- Set API key in environment so the server can read it ---
    if args.key:
        os.environ["API_KEY"] = args.key

    # --- Build player_models map ---
    # random/simple are passed as-is; LLM aliases are left un-resolved here
    # (game_session calls resolve_model internally via create_bot)
    player_models: dict = {}
    for i, spec in enumerate(players):
        if spec != "human":
            player_models[f"player_{i}"] = spec

    # --- Print plan ---
    from bots.openrouter_bot import model_display_name
    print("Texas Hold'em Arena")
    print("=" * 40)
    for i, spec in enumerate(players):
        if spec == "human":
            label = "YOU (human)"
        elif spec in ("random", "simple"):
            label = spec.title() + " Bot (no API key needed)"
        else:
            label = f"{model_display_name(resolve_model(spec))}  [{resolve_model(spec)}]"
        print(f"  player_{i}: {label}")
    print(f"\nBlinds: {args.small_blind}/{args.big_blind}  "
          f"Stack: {args.starting_stack}")
    print(f"Mode: {'Spectator (all AI)' if spectator else 'Human vs. AI'}")
    print()

    # --- Start the FastAPI server in a daemon thread ---
    t = threading.Thread(target=_start_server, args=(args.port,), daemon=True)
    t.start()

    base = f"http://{SERVER_HOST}:{args.port}"
    print(f"Starting server on port {args.port}...")
    if not _wait_for_server(args.port):
        print("Error: server failed to start within timeout.")
        sys.exit(1)
    print("Server ready.")

    # --- Create the game ---
    try:
        game_id = _create_game(base, args, player_models)
    except requests.RequestException as e:
        print(f"Error creating game: {e}")
        sys.exit(1)

    # --- Open browser ---
    url = f"{base}/?game_id={game_id}"
    if spectator:
        url += "&spectator=1"
    print(f"Opening browser: {url}\n")
    webbrowser.open(url)

    # --- Run ---
    if spectator:
        _spectator_loop(base, game_id, args.hands)
    else:
        # Human mode: keep server alive, browser drives everything
        print("Game running. Press Ctrl+C to stop.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
