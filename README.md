# Texas Hold'em Arena

A modular Texas Hold'em engine with a casino-style web UI. Play in the browser, or pit LLM models against each other via [OpenRouter](https://openrouter.ai).

---

## Quick start — browser only (no API key)

```bash
pip install -r requirements.txt
python run.py          # then open http://127.0.0.1:8000
```

Click **New Game**, pick a player count, and play as Player 0 against simple bots. Or use `./run.sh` on macOS/Linux.

---

## Arena launcher (`arena.py`)

`arena.py` is a richer launcher that lets you specify exactly who sits at each seat — you, a random bot, or an LLM model.

### Player types

| Spec | Needs API key? | Description |
|------|---------------|-------------|
| `human` | No | You, in the browser. Must be the first player (seat 0). |
| `random` | No | Picks a uniformly random legal action. |
| `simple` | No | Always checks or calls, never raises. |
| `claude` | Yes | Claude Sonnet via OpenRouter |
| `claude-opus` | Yes | Claude Opus via OpenRouter |
| `claude-haiku` | Yes | Claude Haiku via OpenRouter |
| `gpt-4o` | Yes | GPT-4o via OpenRouter |
| `gpt-4.1` | Yes | GPT-4.1 via OpenRouter |
| `gemini` | Yes | Gemini 2.0 Flash via OpenRouter |
| `gemini-pro` | Yes | Gemini 2.5 Pro via OpenRouter |
| `llama` | Yes | Llama 3.1 70B via OpenRouter |
| `mistral` | Yes | Mistral Large via OpenRouter |
| any OpenRouter ID | Yes | e.g. `anthropic/claude-sonnet-4-6` |

### Without an API key — random bots

```bash
# Watch four random bots play (spectator mode, 20 hands)
python arena.py --players random random random random --hands 20

# Play against three random bots yourself
python arena.py --players human random random random

# Six-player free-for-all, unlimited hands
python arena.py --players random random random random random random
```

> **Spectator mode** is activated automatically when there is no `human` seat.
> `arena.py` drives all moves from the terminal; the browser is a passive viewer.

### With an OpenRouter API key

Pass your key with `--key` or export it as an environment variable:

```bash
export API_KEY=sk-or-v1-...
```

**Human vs. LLMs:**
```bash
python arena.py --players human claude gpt-4o
python arena.py --players human claude claude-opus gemini
python arena.py --players human claude --big-blind 20 --starting-stack 1000
```

**All-LLM spectator mode:**
```bash
python arena.py --players claude gpt-4o gemini llama
python arena.py --players claude gpt-4o gemini llama --hands 50
```

**Mix of types:**
```bash
# You + one LLM + one random bot
python arena.py --players human claude random
```

### All options

```
--key KEY            OpenRouter API key (or env var API_KEY)
--players PLAYER...  Ordered seat list (see table above)
--port PORT          Server port (default: 8000)
--small-blind N      Small blind (default: 5)
--big-blind N        Big blind (default: 10)
--starting-stack N   Starting chips per player (default: 500)
--hands N            Stop after N hands in spectator mode (0 = unlimited)
```

---

## UI features

### Spectator mode layout
When watching an all-AI game, the UI switches to a two-column layout: the poker table on the left, and a **thinking sidebar** on the right. The sidebar shows each model's reasoning and chosen action in real time, and logs every hand result ("Hand 3/10 — Claude +240 with Full house"). The server stays alive after all hands finish so the final leaderboard remains visible.

### Human mode layout
When playing against LLMs, the sidebar becomes an **action log** showing every player's betting actions (Fold, Call, Raise, etc.) across all rounds — useful for tracking what the models did. Bot reasoning is never revealed, keeping the game fair. The final leaderboard shows each model's chip count and failure stats (timeouts, parse errors, guardrail rescues).

### Hand winner display
After each hand the winner and their winning hand rank ("Full house", "Flush", etc.) are shown both in the message area and logged persistently in the sidebar, so you never miss a result even if the next hand starts quickly.

### Side pot support
The engine correctly handles all-in situations with multiple side pots. A player can only win chips proportional to their own contribution — bets made after a player goes all-in form separate side pots contested only by the remaining active players.

### Guardrail LLM
When a model's response can't be parsed, a secondary Claude Sonnet call attempts to rescue the action before falling back to a safe default. The sidebar badges distinguish guardrail rescues (✓) from true fallbacks, and the final leaderboard breaks down each model's failure rate.

---

## Project layout

```
engine/          Game logic: cards, hand evaluation, state, controller
server/          FastAPI server: game API + static file serving
  app.py         Route definitions
  game_session.py  In-memory sessions, bot dispatch
  arena_state.py   Shared arena state (finished flag, summary ack)
bots/            Bot implementations
  random_bot.py  RandomBot — random legal action, no API key
  openrouter_bot.py  OpenRouterBot — calls any LLM via OpenRouter
static/          Browser UI (HTML/CSS/JS), card images
arena.py         CLI launcher for human-vs-AI and all-AI modes
run.py           Simple server-only launcher (no arena features)
scripts/         Helper scripts (download_cards.py, run_human.sh)
```

---

## REST API

The server exposes a plain JSON API, usable from any client:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/games` | Create a game. Body: `num_players`, `small_blind`, `big_blind`, `starting_stack`, `bot_player_ids` (legacy) or `player_models` (`{"player_1": "claude", ...}`). |
| `GET` | `/games/{id}?viewer_id=player_0` | Get state. Pass `viewer_id` to see only that player's hole cards; omit for spectator view (all cards visible). |
| `POST` | `/games/{id}/action` | Submit an action: `{"player_id": "...", "action": {"type": "call", "amount": 10}}`. |
| `POST` | `/games/{id}/bot_move?viewer_id=player_0` | Trigger the current bot (LLM, random, or simple) to act. |
| `POST` | `/games/{id}/next_hand` | Advance to the next hand after `hand_over`. |
| `GET` | `/arena/status` | Arena session status (`game_id`, `spectator`, `finished`). |

### Action types

```json
{"type": "fold"}
{"type": "check"}
{"type": "call",  "amount": 10}
{"type": "raise", "amount": 80}
```

---

## Card images

Card images come from [RL4VLM gym-cards](https://github.com/RL4VLM/RL4VLM/tree/main/gym-cards/gym_cards/envs/img). To download or re-download them:

```bash
python scripts/download_cards.py
```
