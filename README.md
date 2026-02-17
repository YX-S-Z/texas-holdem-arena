# Texas Hold'em Arena

A modular Texas Hold'em engine with a casino-style web UI. Play in the browser, or pit LLM models against each other via [OpenRouter](https://openrouter.ai).

---

## Quick start — browser only (no API key)

```bash
pip install -r requirements.txt
python run.py          # then open http://127.0.0.1:8000
```

Click **New game**, pick a player count, and play as Player 0 against simple bots. Or use `./run.sh` on macOS/Linux.

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

## Project layout

```
engine/          Game logic: cards, hand evaluation, state, controller
server/          FastAPI server: game API + static file serving
  app.py         Route definitions
  game_session.py  In-memory sessions, bot dispatch
bots/            Bot implementations
  random_bot.py  RandomBot — random legal action, no API key
  openrouter_bot.py  OpenRouterBot — calls any LLM via OpenRouter
static/          Browser UI (HTML/CSS/JS), card images
arena.py         CLI launcher for human-vs-AI and all-AI modes
run.py           Simple server-only launcher (no arena features)
```

---

## REST API

The server exposes a plain JSON API, usable from any client:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/games` | Create a game. Body: `num_players`, `small_blind`, `big_blind`, `starting_stack`, `bot_player_ids` (legacy) or `player_models` (`{"player_1": "claude", ...}`). |
| `GET` | `/games/{id}?viewer_id=player_0` | Get state. Only the viewer's hole cards are included. |
| `POST` | `/games/{id}/action` | Submit an action: `{"player_id": "...", "action": {"type": "call", "amount": 10}}`. |
| `POST` | `/games/{id}/bot_move?viewer_id=player_0` | Trigger the current bot (LLM, random, or simple) to act. |
| `POST` | `/games/{id}/next_hand` | Advance to the next hand after `hand_over`. |

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
