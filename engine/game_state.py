"""
Game configuration and state types for Texas Hold'em.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .cards import Card


@dataclass
class GameConfig:
    """Customizable table rules."""
    small_blind: int = 1
    big_blind: int = 2
    min_players: int = 2
    max_players: int = 8
    starting_stack: int = 1000
    min_raise: Optional[int] = None  # None = big blind

    def __post_init__(self) -> None:
        if self.min_players < 2 or self.max_players > 8:
            raise ValueError("Players must be between 2 and 8")
        if self.min_players > self.max_players:
            raise ValueError("min_players must be <= max_players")
        if self.small_blind <= 0 or self.big_blind < self.small_blind:
            raise ValueError("Blinds must be positive and big_blind >= small_blind")
        if self.starting_stack <= 0:
            raise ValueError("starting_stack must be positive")

    @property
    def raise_min(self) -> int:
        return self.min_raise if self.min_raise is not None else self.big_blind


@dataclass
class Player:
    """One player at the table."""
    id: str
    seat: int
    stack: int
    folded: bool = False
    hole_cards: List[Card] = field(default_factory=list)
    display_name: Optional[str] = None
    current_bet: int = 0      # bet this street (reset each street)
    total_committed: int = 0  # total chips put in this hand (for side-pot calculation)

    def to_public_dict(self, show_hole_cards: bool = False) -> Dict[str, Any]:
        """Serializable dict for API. show_hole_cards: include hole cards only for the viewing player (or all at showdown)."""
        d: Dict[str, Any] = {
            "id": self.id,
            "seat": self.seat,
            "stack": self.stack,
            "folded": self.folded,
            "current_bet": self.current_bet,
        }
        if self.display_name is not None:
            d["display_name"] = self.display_name
        if show_hole_cards and self.hole_cards:
            d["hole_cards"] = [c.code for c in self.hole_cards]
        else:
            d["hole_cards"] = []  # or ["?"] * len for hidden
        return d


# Phase / street names for serialization
PHASE_WAITING = "waiting"          # before hand starts
PHASE_PREFLOP = "preflop"
PHASE_FLOP = "flop"
PHASE_TURN = "turn"
PHASE_RIVER = "river"
PHASE_SHOWDOWN = "showdown"
PHASE_HAND_OVER = "hand_over"
