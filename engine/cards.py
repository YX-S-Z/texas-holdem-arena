"""
Playing card and deck for Texas Hold'em.
Card codes match RL4VLM gym-cards img: Suit (C/D/H/S) + Rank (2-9, T, J, Q, K, A).
"""

from dataclasses import dataclass
from enum import Enum
import random
from typing import List, Optional


class Suit(str, Enum):
    CLUBS = "C"
    DIAMONDS = "D"
    HEARTS = "H"
    SPADES = "S"


class Rank(str, Enum):
    TWO = "2"
    THREE = "3"
    FOUR = "4"
    FIVE = "5"
    SIX = "6"
    SEVEN = "7"
    EIGHT = "8"
    NINE = "9"
    TEN = "T"
    JACK = "J"
    QUEEN = "Q"
    KING = "K"
    ACE = "A"


# Order for comparing ranks (Ace high in straights can be handled in evaluator)
RANK_ORDER = [
    Rank.TWO, Rank.THREE, Rank.FOUR, Rank.FIVE, Rank.SIX,
    Rank.SEVEN, Rank.EIGHT, Rank.NINE, Rank.TEN,
    Rank.JACK, Rank.QUEEN, Rank.KING, Rank.ACE,
]


@dataclass(frozen=True)
class Card:
    """A single playing card. code matches RL4VLM image filename (e.g. SA.png)."""
    suit: Suit
    rank: Rank

    @property
    def code(self) -> str:
        """String like 'SA', 'CT' for use with img/cards/{code}.png"""
        return f"{self.suit.value}{self.rank.value}"

    def __str__(self) -> str:
        return self.code

    def rank_index(self) -> int:
        """0 = 2, 12 = A; for comparisons."""
        return RANK_ORDER.index(self.rank)


def make_deck() -> List[Card]:
    """One standard 52-card deck."""
    return [Card(s, r) for s in Suit for r in Rank]


def shuffle_deck(deck: List[Card], rng: Optional[random.Random] = None) -> List[Card]:
    """Return a new shuffled list of the same cards."""
    out = list(deck)
    (rng or random).shuffle(out)
    return out


def card_from_code(code: str) -> Card:
    """Parse a two-char code like 'SA' or 'CT' into a Card."""
    if len(code) != 2:
        raise ValueError(f"Invalid card code: {code}")
    suit_char, rank_char = code[0].upper(), code[1].upper()
    suit = Suit(suit_char)
    rank = Rank(rank_char)
    return Card(suit, rank)
