"""
OpenRouter-backed poker bot.

Each instance represents one seat at the table, backed by a specific LLM.
"""

import json
import re
from typing import Any, Dict, List, Optional

import requests

# Shorthand alias → full OpenRouter model ID
MODEL_ALIASES: Dict[str, str] = {
    "claude":         "anthropic/claude-sonnet-4-6",
    "claude-sonnet":  "anthropic/claude-sonnet-4-6",
    "claude-opus":    "anthropic/claude-opus-4-6",
    "claude-haiku":   "anthropic/claude-haiku-4-5-20251001",
    "gpt-4o":         "openai/gpt-4o",
    "gpt-4.1":        "openai/gpt-4.1",
    "gpt-4":          "openai/gpt-4",
    "gemini":         "google/gemini-2.0-flash-001",
    "gemini-flash":   "google/gemini-2.0-flash-001",
    "gemini-pro":     "google/gemini-2.5-pro-preview-03-25",
    "llama":          "meta-llama/llama-3.1-70b-instruct",
    "llama-70b":      "meta-llama/llama-3.1-70b-instruct",
    "mistral":        "mistralai/mistral-large",
}

# Human-readable display names derived from model ID fragments
_DISPLAY_FRAGMENTS = [
    ("claude-opus",   "Claude Opus"),
    ("claude-haiku",  "Claude Haiku"),
    ("claude-sonnet", "Claude Sonnet"),
    ("claude",        "Claude"),
    ("gpt-4o",        "GPT-4o"),
    ("gpt-4.1",       "GPT-4.1"),
    ("gpt-4",         "GPT-4"),
    ("gemini-2.5",    "Gemini 2.5 Pro"),
    ("gemini-flash",  "Gemini Flash"),
    ("gemini",        "Gemini"),
    ("llama-3.1-70b", "Llama 70B"),
    ("llama",         "Llama"),
    ("mistral-large", "Mistral Large"),
    ("mistral",       "Mistral"),
]

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 20  # seconds


def resolve_model(alias: str) -> str:
    """Resolve a shorthand alias to a full OpenRouter model ID."""
    return MODEL_ALIASES.get(alias, alias)


def model_display_name(model_id: str) -> str:
    """Return a short human-readable name for a model ID."""
    lower = model_id.lower()
    for fragment, name in _DISPLAY_FRAGMENTS:
        if fragment in lower:
            return name
    # Fallback: take the part after the last '/'
    return model_id.split("/")[-1].replace("-", " ").title()


class OpenRouterBot:
    """A poker player backed by an LLM via the OpenRouter API."""

    def __init__(self, api_key: str, model: str):
        """
        Args:
            api_key: OpenRouter API key.
            model:   Full model ID (e.g. "anthropic/claude-sonnet-4-6")
                     or a shorthand alias (e.g. "claude", "gpt-4o").
        """
        self.api_key = api_key
        self.model = resolve_model(model)
        self.display_name = model_display_name(self.model)

    def decide(self, state: Dict[str, Any], player_id: str) -> Dict[str, Any]:
        """
        Given the full game state (as returned by GameController.get_state
        with viewer_id=player_id), return an action dict such as:
            {"type": "fold"}
            {"type": "check"}
            {"type": "call", "amount": 50}
            {"type": "raise", "amount": 150}

        Falls back to the safest legal action if the LLM response cannot
        be parsed or is invalid.
        """
        legal = state.get("legal_actions", [])
        if not legal:
            return {"type": "fold"}

        prompt = self._build_prompt(state, player_id)
        try:
            raw = self._call_api(prompt)
            action = self._parse_response(raw, legal)
        except Exception:
            action = None

        if action is None:
            action = self._fallback_action(legal)
        return action

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, state: Dict[str, Any], player_id: str) -> str:
        phase = state.get("phase", "unknown")
        community = state.get("community_cards", [])
        pot = state.get("pot", 0)
        current_bet = state.get("current_bet_this_round", 0)
        players = state.get("players", [])
        legal = state.get("legal_actions", [])

        # Find this player's info
        me = next((p for p in players if p["id"] == player_id), None)
        my_stack = me["stack"] if me else "?"
        my_bet = me["current_bet"] if me else 0
        my_cards = me.get("hole_cards", []) if me else []

        community_str = " ".join(community) if community else "(none yet)"
        cards_str = " ".join(my_cards) if my_cards else "(unknown)"

        # Describe all players
        player_lines = []
        for p in players:
            tag = " ← YOU" if p["id"] == player_id else ""
            status = " [folded]" if p.get("folded") else ""
            player_lines.append(
                f"  {p.get('display_name', p['id'])}{tag}: "
                f"stack={p['stack']}, bet_this_round={p['current_bet']}{status}"
            )

        # Describe legal actions
        action_lines = []
        for a in legal:
            t = a["type"]
            if t == "raise":
                action_lines.append(
                    f'  raise  (min={a["min_amount"]}, max={a["max_amount"]})'
                )
            elif t == "call":
                action_lines.append(f'  call   (amount={a["amount"]})')
            else:
                action_lines.append(f"  {t}")

        lines = [
            "You are playing Texas Hold'em poker. Make the best strategic decision.",
            "",
            f"Phase:             {phase}",
            f"Community cards:   {community_str}",
            f"Your hole cards:   {cards_str}",
            f"Pot:               {pot}",
            f"Current bet:       {current_bet}  (you have put in {my_bet}, so you owe {max(0, current_bet - my_bet)})",
            f"Your stack:        {my_stack}",
            "",
            "All players:",
            *player_lines,
            "",
            "Legal actions:",
            *action_lines,
            "",
            "Respond with ONLY a JSON object — no explanation, no markdown:",
            '  {"action": "fold"}',
            '  {"action": "check"}',
            '  {"action": "call"}',
            '  {"action": "raise", "amount": <integer>}',
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # API call
    # ------------------------------------------------------------------

    def _call_api(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 80,
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
        """Extract JSON from LLM response and validate against legal actions."""
        # Find first {...} block
        match = re.search(r"\{[^{}]+\}", text)
        if not match:
            return None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return None

        action_type = data.get("action")
        if not action_type:
            return None

        legal_types = {a["type"] for a in legal}
        if action_type not in legal_types:
            return None

        if action_type == "raise":
            raise_info = next((a for a in legal if a["type"] == "raise"), None)
            if raise_info is None:
                return None
            amount = data.get("amount")
            if not isinstance(amount, (int, float)):
                # Default to min raise
                amount = raise_info["min_amount"]
            amount = int(amount)
            amount = max(raise_info["min_amount"], min(raise_info["max_amount"], amount))
            return {"type": "raise", "amount": amount}

        if action_type == "call":
            call_info = next((a for a in legal if a["type"] == "call"), None)
            if call_info is None:
                return None
            return {"type": "call", "amount": call_info["amount"]}

        if action_type in ("check", "fold"):
            return {"type": action_type}

        return None

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_action(legal: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Prefer check > call > fold when LLM fails."""
        types = {a["type"]: a for a in legal}
        if "check" in types:
            return {"type": "check"}
        if "call" in types:
            return {"type": "call", "amount": types["call"]["amount"]}
        return {"type": "fold"}
