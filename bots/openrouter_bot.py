"""
OpenRouter-backed poker bot.

Each instance represents one seat at the table, backed by a specific LLM.
The system prompt establishes the bot's role as a poker player.
The user message contains the current game state visible only to this player —
hole cards are scoped per-player by the game engine (viewer_id), so no bot
ever sees another player's hole cards.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Model registry — single source of truth.
#
# Each entry: alias → (openrouter_model_id, display_name)
#
# Rules:
#   • The alias is what you type on the CLI / in run_arena.sh.
#   • Multiple aliases can share the same model ID (e.g. short + long form).
#   • The display_name is shown in the browser — no substring-matching needed.
#   • To add a new model: one line here, nothing else to change.
# ---------------------------------------------------------------------------

_MODEL_REGISTRY: Dict[str, Tuple[str, str]] = {
    # ── Anthropic ────────────────────────────────────────────────────────────
    "claude":        ("anthropic/claude-sonnet-4-6",         "Sonnet 4.6"),
    "claude-opus":   ("anthropic/claude-opus-4-6",           "Opus 4.6"),
    "claude-haiku":  ("anthropic/claude-haiku-4-5-20251001", "Haiku 4.5"),
    # ── OpenAI ───────────────────────────────────────────────────────────────
    "gpt":           ("openai/gpt-5.2",      "GPT 5.2"),
    "gpt-5.2-chat":  ("openai/gpt-5.2-chat", "GPT 5.2 Chat"),
    "gpt-5":         ("openai/gpt-5",        "GPT 5"),
    "gpt-5-nano":    ("openai/gpt-5-nano",   "GPT 5 Nano"),
    "gpt-5-mini":    ("openai/gpt-5-mini",   "GPT 5 Mini"),
    "gpt-oss":       ("openai/gpt-oss-120b", "GPT OSS"),
    "gpt-4o":        ("openai/gpt-4o",       "GPT 4o"),
    # ── Google ───────────────────────────────────────────────────────────────
    "gemini":        ("google/gemini-3-flash-preview", "Gemini 3 Flash"),
    "gemini-pro":    ("google/gemini-3-pro-preview",   "Gemini 3 Pro"),
    # ── Qwen ─────────────────────────────────────────────────────────────────
    "qwen":          ("qwen/qwen3-max", "Qwen3 Max"),
    "qwen-3.5":     ("qwen/qwen3.5-397b-a17b", "Qwen3.5"),
    "qwen-32b":      ("qwen/qwen3-32b", "Qwen3 32B"),
    # ── Moonshot ─────────────────────────────────────────────────────────────
    "kimi":          ("moonshotai/kimi-k2.5", "Kimi K2.5"),
    # ── DeepSeek ─────────────────────────────────────────────────────────────
    "deepseek":      ("deepseek/deepseek-v3.2", "DeepSeek 3.2"),
    # ── xAI ──────────────────────────────────────────────────────────────────
    "grok":          ("x-ai/grok-4",        "Grok 4"),
    "grok-fast":     ("x-ai/grok-4.1-fast", "Grok 4.1 fast"),
    # ── Minimax ──────────────────────────────────────────────────────────────
    "minimax":       ("minimax/minimax-m2.5", "Minimax M2.5"),
    # ── Z-AI ──────────────────────────────────────────────────────────────────
    "glm-5":    ("z-ai/glm-5", "GLM 5"),
    # ── Meta ──────────────────────────────────────────────────────────────────
    "llama-4": ("meta-llama/llama-4-maverick", "LLAMA 4"),
}

# Derived: alias → model_id  (used by resolve_model and the arg-parser help text)
MODEL_ALIASES: Dict[str, str] = {
    alias: model_id for alias, (model_id, _) in _MODEL_REGISTRY.items()
}

# Derived: model_id → display_name  (first registration wins for duplicate IDs)
_ID_TO_NAME: Dict[str, str] = {}
for _alias, (_mid, _dname) in _MODEL_REGISTRY.items():
    _ID_TO_NAME.setdefault(_mid, _dname)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# (connect_timeout, read_timeout) — read timeout is per-chunk inactivity,
# not total time; 10s to establish the connection, 45s between chunks.
REQUEST_TIMEOUT = (10, 45)

# Model used to rescue unparseable responses from other LLMs.
GUARDRAIL_MODEL = "anthropic/claude-sonnet-4-6"

# On parse error, retry the main model this many times before guardrail/fallback.
PARSE_ERROR_MAX_RETRIES = 5

# ---------------------------------------------------------------------------
# System prompt — establishes the bot's role for the entire session
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a Texas Hold'em poker player. Play to the best of your ability.
You can only see your own hole cards — other players' cards are hidden.

Respond with brief reasoning, then your chosen action as JSON on the final line.
No markdown, no code fences. The JSON must be the very last thing you write.

JSON format:
  {"action": "fold"}
  {"action": "check"}
  {"action": "call"}
  {"action": "raise", "amount": <integer>}

Examples:

My hand is strong preflop.
{"action": "raise", "amount": 80}

I'm behind and the pot odds don't justify calling.
{"action": "fold"}

No bet to face — I'll take the free card.
{"action": "check"}

Good pot odds to continue.
{"action": "call"}
"""

# ---------------------------------------------------------------------------
# Parser helpers — module-level for clarity
# ---------------------------------------------------------------------------

# Alternative key names LLMs sometimes use instead of "action"
_ACTION_KEY_ALIASES = ("action", "move", "decision", "play", "choice", "type")

# Synonym map → canonical engine action name
_ACTION_SYNONYMS: Dict[str, str] = {
    "fold":  "fold",
    "check": "check",
    "call":  "call",
    "raise": "raise",
    "bet":   "raise",   # very common poker synonym
    "pass":  "check",   # occasional alias for check
    "allin": "raise",
    "all-in": "raise",
    "all_in": "raise",
}


def resolve_model(alias: str) -> str:
    """Resolve a CLI alias to a full OpenRouter model ID.
    Passes unknown strings through unchanged so raw model IDs work too."""
    return MODEL_ALIASES.get(alias, alias)


def model_display_name(model_id: str) -> str:
    """Return the concise display name for a model ID.
    Falls back to the raw slug for model IDs not in the registry."""
    return _ID_TO_NAME.get(model_id) or model_id.split("/")[-1].replace("-", " ")


class OpenRouterBot:
    """A poker player backed by an LLM via the OpenRouter API."""

    def __init__(self, api_key: str, model: str):
        """
        Args:
            api_key: OpenRouter API key.
            model:   Full model ID or shorthand alias (e.g. "claude", "gpt-4o").
        """
        self.api_key = api_key
        self.model = resolve_model(model)
        self.display_name = model_display_name(self.model)

    def decide(self, state: Dict[str, Any], player_id: str) -> Dict[str, Any]:
        """
        Given game state scoped to this player (viewer_id=player_id, so only
        this player's hole cards are visible), return an action dict:
            {"type": "fold"}
            {"type": "check"}
            {"type": "call", "amount": 50}
            {"type": "raise", "amount": 150}

        Also sets:
          self.last_thinking      — the model's reasoning sentence (or None)
          self.last_failure_reason — None on success, or one of:
              "timeout"      API call exceeded REQUEST_TIMEOUT
              "parse_error"  Response received but no valid JSON action found
              "api_error"    Any other exception (HTTP error, network, etc.)

        Falls back to the safest legal action (check > call > fold) on any failure.
        """
        legal = state.get("legal_actions", [])
        self.last_thinking: Optional[str] = None
        self.last_failure_reason: Optional[str] = None
        self.last_raw_response: Optional[str] = None

        if not legal:
            return {"type": "fold"}

        user_msg = self._build_user_message(state, player_id)
        action = None
        last_raw: Optional[str] = None
        try:
            for attempt in range(1 + PARSE_ERROR_MAX_RETRIES):
                raw = self._call_api(user_msg)
                self.last_raw_response = raw
                last_raw = raw
                self.last_thinking = self._extract_thinking(raw)
                action = self._parse_response(raw, legal)
                if action is not None:
                    break
                # Parse failed; retry unless we've exhausted retries.
                if attempt >= PARSE_ERROR_MAX_RETRIES:
                    break

            if action is None and last_raw:
                # All retries failed — try the guardrail rescue call.
                guardrail_action = self._guardrail_parse(last_raw, legal)
                if guardrail_action is not None:
                    self.last_failure_reason = "parse_error_rescued"
                    action = guardrail_action
                else:
                    self.last_failure_reason = "parse_error"
            elif action is None:
                self.last_failure_reason = "parse_error"
        except requests.exceptions.Timeout:
            self.last_failure_reason = "timeout"
        except Exception:
            self.last_failure_reason = "api_error"

        return action if action is not None else self._fallback_action(legal)

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_user_message(self, state: Dict[str, Any], player_id: str) -> str:
        """
        Build the per-turn user message.  The system prompt is sent separately
        so models that support system roles receive the role context once,
        cleanly separated from the per-hand game state.

        Card privacy: the game engine already scopes hole_cards to viewer_id,
        so other players always show hole_cards=[] here.
        """
        phase = state.get("phase", "unknown")
        community = state.get("community_cards", [])
        pot = state.get("pot", 0)
        current_bet = state.get("current_bet_this_round", 0)
        players = state.get("players", [])
        legal = state.get("legal_actions", [])
        bb = (state.get("config") or {}).get("big_blind", "?")

        me = next((p for p in players if p["id"] == player_id), None)
        my_stack = me["stack"] if me else "?"
        my_bet = me["current_bet"] if me else 0
        my_cards = me.get("hole_cards", []) if me else []
        my_name = me.get("display_name", player_id) if me else player_id

        community_str = "  ".join(community) if community else "(none yet)"
        cards_str = "  ".join(my_cards) if my_cards else "(not dealt yet)"
        owe = max(0, current_bet - my_bet)

        # Opponents — show stacks/bets/status but NOT their cards
        opp_lines = []
        for p in players:
            if p["id"] == player_id:
                continue
            status = " [folded]" if p.get("folded") else ""
            opp_lines.append(
                f"  {p.get('display_name', p['id'])}: "
                f"stack={p['stack']}, bet={p['current_bet']}{status}"
            )

        # Legal actions — show the exact JSON to output for each choice
        action_lines = []
        for a in legal:
            t = a["type"]
            if t == "raise":
                action_lines.append(
                    f'  {{"action": "raise", "amount": N}}   '
                    f"where N is an integer {a['min_amount']}–{a['max_amount']}"
                )
            elif t == "call":
                action_lines.append(
                    f'  {{"action": "call"}}   (costs {a["amount"]} chips)'
                )
            elif t == "check":
                action_lines.append('  {"action": "check"}')
            elif t == "fold":
                action_lines.append('  {"action": "fold"}')

        lines = [
            f"=== Your turn — {my_name} ===",
            "",
            f"Street:          {phase}",
            f"Big blind:       {bb}",
            f"Community cards: {community_str}",
            f"Your hole cards: {cards_str}   ← only you can see these",
            f"Pot:             {pot}",
            f"Current bet:     {current_bet}  (you've put in {my_bet}, you owe {owe})",
            f"Your stack:      {my_stack}",
            "",
            "Opponents:",
            *opp_lines,
            "",
            "Available actions (copy the JSON exactly, fill in N for raise):",
            *action_lines,
            "",
            "Reason briefly, then output your chosen JSON on the final line:",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Thinking extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_thinking(raw: str) -> Optional[str]:
        """
        The model is asked to write one reasoning sentence, then a JSON line.
        This strips the JSON from the end and returns whatever came before it
        as the "thinking" text. Returns None if nothing meaningful is found.
        """
        # Remove the last {...} block and any trailing whitespace
        thinking = re.sub(r"\{[^{}]+\}\s*$", "", raw).strip()
        # Collapse internal newlines to a single space and trim
        thinking = " ".join(thinking.split())
        return thinking if thinking else None

    # ------------------------------------------------------------------
    # API call
    # ------------------------------------------------------------------

    def _call_api(self, user_message: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            "temperature": 0.7,
            "max_tokens": 200,
        }
        resp = requests.post(
            OPENROUTER_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(
        self, text: str, legal: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Fuzzy extraction of a legal poker action from raw LLM output.

        Strategy (in order):
          1. Strip markdown fences.
          2. Find all {...} blocks; try each from last to first, with both
             standard JSON and a single-quote fallback.
          3. If no JSON block yields an action, scan the last few lines of
             plain text for action keywords (last resort before guardrail).
        """
        legal_types = {a["type"] for a in legal}

        # 1. Strip markdown code fences
        cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "")

        # 2. Try every {...} block, last-first (JSON is asked to be at the end)
        for candidate in reversed(re.findall(r"\{[^{}]+\}", cleaned)):
            action = self._try_json_block(candidate, legal_types, legal)
            if action:
                return action
            # Single-quote fallback for models that write {'action': 'fold'}
            action = self._try_json_block(candidate.replace("'", '"'), legal_types, legal)
            if action:
                return action

        # 3. No valid JSON found — try plain-text keyword scan
        return self._extract_from_plain_text(cleaned, legal_types, legal)

    def _try_json_block(
        self,
        candidate: str,
        legal_types: set,
        legal: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Attempt to extract a valid action from one JSON-like string."""
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return None

        # Find the action value — tolerate alternative key names
        raw_value = None
        for key in _ACTION_KEY_ALIASES:
            val = data.get(key)
            if val and isinstance(val, str):
                raw_value = val.strip().lower()
                break
        if not raw_value:
            return None

        # Normalise synonyms and handle compound values like "raise 80"
        amount_hint: Optional[int] = None
        action_type = _ACTION_SYNONYMS.get(raw_value)
        if action_type is None and " " in raw_value:
            # e.g. "raise to 80", "bet 120", "fold now"
            first_word = raw_value.split()[0]
            action_type = _ACTION_SYNONYMS.get(first_word)
            m = re.search(r"\d+", raw_value)
            if m:
                amount_hint = int(m.group())
        if not action_type or action_type not in legal_types:
            return None

        # Resolve amount: prefer explicit JSON field, then hint from compound value
        if action_type == "raise":
            raw_amount = data.get("amount") or data.get("bet") or amount_hint
            if isinstance(raw_amount, str):
                m = re.search(r"\d+", raw_amount)
                raw_amount = int(m.group()) if m else None
            raise_info = next((a for a in legal if a["type"] == "raise"), None)
            if raise_info is None:
                return None
            amt = int(raw_amount) if raw_amount is not None else raise_info["min_amount"]
            amt = max(raise_info["min_amount"], min(raise_info["max_amount"], amt))
            return {"type": "raise", "amount": amt}

        if action_type == "call":
            call_info = next((a for a in legal if a["type"] == "call"), None)
            return {"type": "call", "amount": call_info["amount"]} if call_info else None

        return {"type": action_type}  # fold / check

    def _extract_from_plain_text(
        self,
        text: str,
        legal_types: set,
        legal: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Absolute last resort before the guardrail: scan the final 3 lines of
        the response for a recognisable poker action keyword or raise amount.
        Only the tail of the text is checked so that reasoning mentions of
        actions (e.g. "I considered folding…") don't pollute the result.
        """
        tail = "\n".join(text.strip().splitlines()[-3:]).lower()

        # Raise/bet with explicit amount (most specific — try first)
        if "raise" in legal_types:
            m = re.search(r"\b(?:raise|bet)\s+(?:to\s+)?(\d+)", tail)
            if m:
                raise_info = next((a for a in legal if a["type"] == "raise"), None)
                if raise_info:
                    amt = max(raise_info["min_amount"],
                              min(raise_info["max_amount"], int(m.group(1))))
                    return {"type": "raise", "amount": amt}

        # Simple keyword scan in priority order
        for action in ("fold", "check", "call", "raise"):
            if action not in legal_types:
                continue
            if re.search(rf"\b{action}\b", tail):
                if action == "call":
                    call_info = next((a for a in legal if a["type"] == "call"), None)
                    return {"type": "call", "amount": call_info["amount"]} if call_info else None
                if action == "raise":
                    raise_info = next((a for a in legal if a["type"] == "raise"), None)
                    if raise_info:
                        return {"type": "raise", "amount": raise_info["min_amount"]}
                    continue
                return {"type": action}

        # "bet" as synonym for raise
        if "raise" in legal_types and re.search(r"\bbet\b", tail):
            raise_info = next((a for a in legal if a["type"] == "raise"), None)
            if raise_info:
                m = re.search(r"\bbet\s+(?:to\s+)?(\d+)", tail)
                amt = int(m.group(1)) if m else raise_info["min_amount"]
                amt = max(raise_info["min_amount"], min(raise_info["max_amount"], amt))
                return {"type": "raise", "amount": amt}

        return None

    # ------------------------------------------------------------------
    # Guardrail rescue
    # ------------------------------------------------------------------

    def _guardrail_parse(
        self, raw: str, legal: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        When the primary LLM response cannot be parsed, ask Claude Sonnet to
        extract a valid action JSON from the raw text.  Returns None if the
        rescue call itself fails or also produces unparseable output.
        """
        legal_types = [a["type"] for a in legal]
        raise_info = next((a for a in legal if a["type"] == "raise"), None)
        constraints = f"Legal actions: {legal_types}."
        if raise_info:
            constraints += (
                f" For raise: min={raise_info['min_amount']}, max={raise_info['max_amount']}."
            )

        prompt = (
            f"A poker bot produced the following response that failed to parse:\n\n"
            f"{raw}\n\n"
            f"{constraints}\n\n"
            f"Extract the intended poker action and respond with ONLY a single-line "
            f'JSON object. Use {{"action":"fold"}}, {{"action":"check"}}, '
            f'{{"action":"call"}}, or {{"action":"raise","amount":N}}. No other text.'
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": GUARDRAIL_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 50,
        }
        try:
            resp = requests.post(
                OPENROUTER_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            return self._parse_response(text, legal)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_action(legal: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Prefer check > call > fold when the LLM response cannot be used."""
        types = {a["type"]: a for a in legal}
        if "check" in types:
            return {"type": "check"}
        if "call" in types:
            return {"type": "call", "amount": types["call"]["amount"]}
        return {"type": "fold"}
