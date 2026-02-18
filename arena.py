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
# Display name derivation
# ---------------------------------------------------------------------------

def _make_display_names(players: list) -> dict:
    """
    Build a {player_id: display_name} map from the raw player spec list.

    Rules:
      - "human"  → skipped (frontend labels it "You")
      - "random" → "Random Bot", or "Random Bot 1/2/3..." when there are multiples
      - "simple" → "Simple Bot", or "Simple Bot 1/2/3..."
      - anything else → the spec as typed, capitalising the first letter;
        if multiples share the same spec they also get numbered
    """
    from collections import Counter
    # Count how many times each non-human spec appears
    spec_count = Counter(s for s in players if s != "human")

    seen: Counter = Counter()
    names: dict = {}
    for i, spec in enumerate(players):
        if spec == "human":
            continue
        pid = f"player_{i}"
        seen[spec] += 1
        total = spec_count[spec]

        if spec == "random":
            base_name = "Random Bot"
        elif spec == "simple":
            base_name = "Simple Bot"
        else:
            # Use the spec as-is. Only capitalise the first character when
            # it starts with a plain lowercase letter (not a digit or symbol).
            base_name = (spec[0].upper() + spec[1:]) if spec and spec[0].isalpha() else spec

        names[pid] = f"{base_name} {seen[spec]}" if total > 1 else base_name

    return names


# ---------------------------------------------------------------------------
# Game management helpers
# ---------------------------------------------------------------------------

def _create_game(base: str, args: argparse.Namespace,
                 player_models: dict, player_names: dict) -> str:
    body = {
        "num_players": len(args.players),
        "small_blind": args.small_blind,
        "big_blind": args.big_blind,
        "starting_stack": args.starting_stack,
        "player_models": player_models,
        "player_names": player_names,
    }
    r = requests.post(f"{base}/games", json=body, timeout=5)
    r.raise_for_status()
    return r.json()["game_id"]


def _get_state(base: str, game_id: str) -> dict:
    r = requests.get(f"{base}/games/{game_id}", timeout=10)
    r.raise_for_status()
    return r.json()


def _bot_move(base: str, game_id: str) -> bool:
    """Trigger one bot action. Returns True on success, False on timeout/error."""
    try:
        # 120s: LLM API calls can be legitimately slow; give them room to breathe.
        r = requests.post(f"{base}/games/{game_id}/bot_move", timeout=120)
        data = r.json() if r.ok else {}
        la = data.get("last_action") or {}
        fr = la.get("failure_reason")
        if fr:
            name = la.get("display_name", "?")
            print(f"[arena] {name}: {fr}")
            raw = la.get("raw_response")
            if raw:
                print(f"[arena]   raw LLM output: {raw}")
        return True
    except requests.exceptions.ReadTimeout:
        print("[arena] bot_move timed out waiting for LLM — skipping turn")
        return False
    except requests.RequestException as e:
        print(f"[arena] bot_move error: {e}")
        return False


def _next_hand(base: str, game_id: str) -> None:
    requests.post(f"{base}/games/{game_id}/next_hand", timeout=5)


# ---------------------------------------------------------------------------
# Spectator polling loop
# ---------------------------------------------------------------------------

def _register_arena(base: str, game_id: str, spectator: bool) -> None:
    """Register this arena session with the server (for browser restart support)."""
    try:
        requests.post(
            f"{base}/arena/register",
            json={"game_id": game_id, "spectator": spectator},
            timeout=5,
        )
    except requests.RequestException:
        pass


def _finish_arena(base: str) -> None:
    """Signal the server (and browser) that the arena session is over."""
    try:
        requests.post(f"{base}/arena/finish", timeout=5)
    except requests.RequestException:
        pass


def _check_arena_restart(base: str, current_game_id: str) -> str:
    """Poll /arena/status; return the server's current game_id (may differ if browser restarted)."""
    try:
        r = requests.get(f"{base}/arena/status", timeout=3)
        return r.json().get("game_id") or current_game_id
    except requests.RequestException:
        return current_game_id


def _spectator_loop(base: str, initial_game_id: str, max_hands: int) -> None:
    """Drive all bot moves and hand transitions. Runs in the main thread."""
    game_id = initial_game_id
    hands_played = 0
    print(f"\nSpectator mode — watching game {game_id}")
    if max_hands:
        print(f"Will stop after {max_hands} hands. Press Ctrl+C to stop early.\n")
    else:
        print("Press Ctrl+C to stop.\n")

    last_phase = None
    last_current = None
    arena_check_ticks = 0  # how many ticks since last /arena/status check

    try:
        while True:
            # Periodically check if the browser triggered a restart
            arena_check_ticks += 1
            if arena_check_ticks >= 10:
                arena_check_ticks = 0
                server_gid = _check_arena_restart(base, game_id)
                if server_gid != game_id:
                    game_id = server_gid
                    hands_played = 0
                    last_phase = None
                    last_current = None
                    print(f"\n[arena] Browser restart — switched to game {game_id}\n")

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
                # Check if all but one player have busted (natural game end)
                active_players = [p for p in state.get("players", []) if p.get("stack", 0) > 0]
                natural_end = len(active_players) <= 1

                if (max_hands and hands_played >= max_hands) or natural_end:
                    reason = "natural game end" if natural_end else f"{hands_played} hand(s)"
                    print(f"\nGame over ({reason}). Signalling browser...")
                    _finish_arena(base)
                    print("Leaderboard is live. Server staying up — press Ctrl+C to stop.")
                    while True:
                        time.sleep(60)
                time.sleep(1.0)
                _next_hand(base, game_id)
                last_phase = None
                last_current = None
            elif current is not None:
                ok = _bot_move(base, game_id)
                # On timeout/error the server may have applied a fallback action
                # or not; either way, the next state fetch will catch up.
                time.sleep(POLL_INTERVAL if ok else 1.0)
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

    # --- Build player_models map and display names ---
    player_models: dict = {}
    for i, spec in enumerate(players):
        if spec != "human":
            player_models[f"player_{i}"] = spec

    player_names = _make_display_names(players)

    # --- Print plan ---
    print("Texas Hold'em Arena")
    print("=" * 40)
    for i, spec in enumerate(players):
        pid = f"player_{i}"
        if spec == "human":
            label = "YOU (human)"
        else:
            display = player_names.get(pid, spec)
            if spec not in ("random", "simple"):
                label = f"{display}  [{resolve_model(spec)}]"
            else:
                label = f"{display}  (no API key needed)"
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
        game_id = _create_game(base, args, player_models, player_names)
    except requests.RequestException as e:
        print(f"Error creating game: {e}")
        sys.exit(1)

    # --- Register arena session with the server (enables browser restart) ---
    _register_arena(base, game_id, spectator)

    # --- Open browser ---
    url = f"{base}/?game_id={game_id}&arena=1"
    if spectator:
        url += "&spectator=1"
    if args.hands > 0:
        url += f"&hands={args.hands}"
    print(f"Opening browser: {url}\n")
    webbrowser.open(url)

    # --- Run ---
    if spectator:
        _spectator_loop(base, game_id, args.hands)  # game_id = initial game
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
