# Texas Hold'em Arena

A browser-based Texas Hold'em arena where LLM models (Claude, GPT, Gemini, DeepSeek, …) play poker against each other — or against you.

---

## Quick Start

### 1. Prerequisites

- Python 3.8+
- An **OpenRouter API key** — get one free at [openrouter.ai](https://openrouter.ai)

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

---

## Setting your API key

All run commands use the inline environment-variable syntax supported by bash/zsh:

```bash
API_KEY="your-openrouter-api-key-here" \
  python arena.py ...
```

Replace `your-openrouter-api-key-here` with your key (starts with `sk-or-v1-`). The key is set only for that single command — it is never written to disk.

> **Alternative**: export it once for the session and omit it from the command:
> ```bash
> export API_KEY="your-openrouter-api-key-here"
> python arena.py --players human claude gpt
> ```

---

## Mode 1 — Spectator: watch AI models battle each other

All seats are LLMs; `arena.py` drives every move automatically. Just open the browser and watch.

```bash
API_KEY="your-openrouter-api-key-here" \
  python arena.py --players claude gpt gemini qwen-3.5 kimi deepseek grok-fast glm-5 \
  --hands 25
```

This is equivalent to running `scripts/run_arena.sh` with your key substituted in.

What happens:
- A browser tab opens automatically at `http://127.0.0.1:8000`
- All 8 models play 25 hands autonomously
- A leaderboard overlay appears when the session ends
- Press **Ctrl+C** in the terminal to stop early

---

## Mode 2 — Human vs. AI: play against the models yourself

`human` must be the first player. A browser tab opens where you click your own actions.

```bash
API_KEY="your-openrouter-api-key-here" \
  python arena.py --players human claude gpt gemini deepseek grok-fast \
  --hands 2 \
  --port 8001
```

This is equivalent to running `scripts/run_human.sh` with your key substituted in.

What happens:
- A browser tab opens at `http://127.0.0.1:8001`
- You are **player 0** (highlighted in blue)
- Click **Fold / Check / Call / Raise** to act; bots move automatically
- After each hand, click **Next hand** to continue
- Press **Ctrl+C** in the terminal to stop

> **"New Game" button**: clicking this restarts the entire session from scratch — all hands reset to 0, every player's stack returns to the starting amount, and a fresh game with the same configuration begins. Use it when you want a full rematch after the session ends.

---

## Advanced example — full configuration

```bash
API_KEY="your-openrouter-api-key-here" \
  python arena.py \
  --players human claude gpt gemini deepseek \
  --hands 20 \
  --small-blind 10 \
  --big-blind 20 \
  --starting-stack 1000 \
  --port 8080
```

| Flag | Default | Description |
|---|---|---|
| `--players` | `human claude` | Ordered seat list (see models below) |
| `--hands` | 0 (unlimited) | Stop after this many hands |
| `--small-blind` | 5 | Small blind amount |
| `--big-blind` | 10 | Big blind amount |
| `--starting-stack` | 500 | Starting chips per player |
| `--port` | 8000 | Local port for the web UI |
| `--key` | env `API_KEY` | API key (alternative to env var) |

---

## Available models

| Alias | Model |
|---|---|
| `claude` | Claude Sonnet 4.6 |
| `claude-opus` | Claude Opus 4.6 |
| `claude-haiku` | Claude Haiku 4.5 |
| `gpt` | GPT-5.2 |
| `gpt-5` | GPT-5 |
| `gpt-4o` | GPT-4o |
| `gemini` / `gemini-flash` | Gemini 3 Flash |
| `gemini-pro` | Gemini 3 Pro |
| `qwen` | Qwen3 Max |
| `qwen-3.5` | Qwen3.5 |
| `qwen-32b` | Qwen3 32B |
| `kimi` | Kimi K2.5 |
| `deepseek` | DeepSeek V3.2 |
| `grok` | Grok 4 |
| `grok-fast` | Grok 4.1 fast |
| `minimax` | Minimax M2.5 |
| `glm-5` | GLM 5 |
| `llama-4` | LLaMA 4 Maverick |

You can also pass any full OpenRouter model ID directly (e.g. `anthropic/claude-sonnet-4-6`).

---

## Playing without an API key

Use `random` or `simple` bots — no key required:

```bash
# Random bots only
python arena.py --players random random random random --hands 10

# Human vs. random bots
python arena.py --players human random random
```

- `random` — picks a uniformly random legal action
- `simple` — always checks or calls, never raises
