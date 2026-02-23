"""Bot factory."""

from typing import Any, Optional

from .random_bot import RandomBot
from .openrouter_bot import OpenRouterBot, resolve_model, model_display_name


def create_bot(spec: str, api_key: Optional[str] = None, bluff_mode: bool = False) -> Any:
    """
    Return a bot instance for the given player spec.

    spec:
      "random"          → RandomBot (no API key needed)
      "simple"          → None (signals the legacy check/call/fold bot)
      anything else     → OpenRouterBot with the resolved model ID
    """
    if spec == "random":
        return RandomBot()
    if spec == "simple":
        return None  # handled by _simple_action in game_session
    return OpenRouterBot(api_key=api_key or "", model=spec, bluff_mode=bluff_mode)
