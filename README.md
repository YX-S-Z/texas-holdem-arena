# Texas Hold'em Arena

A modular Texas Hold'em game engine with a web UI. Play in the browser against a simple bot, or hook up LLM players later.

## Run the game in your browser

**Option A – shell script (macOS/Linux):**
```bash
cd texas_holdem_arena
./run.sh
```

**Option B – Python:**
```bash
cd texas_holdem_arena
pip install -r requirements.txt
python run.py
```

Then open **http://127.0.0.1:8000** in your browser and click **New game** to start. You play as Player 0 against a bot (Player 1).

## Project layout

- **engine/** – Game logic (cards, hand evaluation, game state, controller). Configurable small/big blind via `GameConfig`.
- **server/** – FastAPI app: create game, get state, submit action, bot move. Serves the web UI.
- **static/** – Single-page UI: table, community cards, pot, players, and action buttons. Card images (from [RL4VLM gym-cards](https://github.com/RL4VLM/RL4VLM/tree/main/gym-cards/gym_cards/envs/img)) live in `static/img/cards/`. To (re)download them: `python scripts/download_cards.py`.

## API (for LLM or other clients)

- `POST /games` – Create game (body: `num_players`, `small_blind`, `big_blind`, `starting_stack`, `bot_player_ids`).
- `GET /games/{game_id}?viewer_id=player_0` – Get state (only that player’s hole cards).
- `POST /games/{game_id}/action` – Submit action: `{"player_id": "...", "action": {"type": "call", "amount": 5}}`.
- `POST /games/{game_id}/bot_move?viewer_id=player_0` – Let the bot act and return new state.
