"""Random bot — picks a uniformly random legal action. No API key needed."""

import random
from typing import Any, Dict, List


class RandomBot:
    """Plays randomly. Useful for testing without an API key."""

    display_name = "Random Bot"

    def decide(self, state: Dict[str, Any], player_id: str) -> Dict[str, Any]:
        legal = state.get("legal_actions", [])
        if not legal:
            return {"type": "fold"}
        action = random.choice(legal)
        if action["type"] == "raise":
            amount = random.randint(action["min_amount"], action["max_amount"])
            return {"type": "raise", "amount": amount}
        if action["type"] == "call":
            return {"type": "call", "amount": action["amount"]}
        return {"type": action["type"]}
