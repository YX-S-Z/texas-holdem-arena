"""
Evaluate the best 5-card poker hand from 5-7 cards.
Hand ranks: High card, Pair, Two pair, Three of a kind, Straight, Flush,
Full house, Four of a kind, Straight flush.
"""

from itertools import combinations
from typing import List, Tuple

from .cards import Card, RANK_ORDER, Rank


# Hand type rank (higher = better). High card = 0, straight flush = 8.
HAND_RANK_HIGH_CARD = 0
HAND_RANK_PAIR = 1
HAND_RANK_TWO_PAIR = 2
HAND_RANK_THREE_OF_A_KIND = 3
HAND_RANK_STRAIGHT = 4
HAND_RANK_FLUSH = 5
HAND_RANK_FULL_HOUSE = 6
HAND_RANK_FOUR_OF_A_KIND = 7
HAND_RANK_STRAIGHT_FLUSH = 8


def rank_index(r: Rank) -> int:
    return RANK_ORDER.index(r)


def _ranks(cards: List[Card]) -> List[int]:
    return [rank_index(c.rank) for c in cards]


def _is_flush(cards: List[Card]) -> bool:
    return len(set(c.suit for c in cards)) == 1


def _is_straight(rank_indices: List[int]) -> Tuple[bool, int]:
    """Return (is_straight, high_rank_index). Ace-low straight (A-2-3-4-5) has high_rank_index for 5."""
    s = sorted(set(rank_indices))
    if len(s) < 5:
        return False, -1
    # Check for A-2-3-4-5 (wheel)
    if s == [0, 1, 2, 3, 12]:  # 2,3,4,5,A
        return True, 3  # high card of wheel is 5
    # Find best (highest) straight: i in [0, len(s)-4) so s[i] and s[i+4] are always in range
    best_high = -1
    for i in range(len(s) - 4):
        if i + 4 < len(s) and s[i + 4] - s[i] == 4:
            best_high = s[i + 4]
    return (best_high >= 0, best_high)


def _evaluate_five(cards: List[Card]) -> Tuple[int, Tuple[int, ...]]:
    """
    Evaluate exactly 5 cards. Return (hand_type, tiebreaker_tuple).
    tiebreaker_tuple: for comparisons, higher values first (e.g. pair rank then kickers).
    """
    if len(cards) != 5:
        raise ValueError("Need exactly 5 cards")
    ranks = _ranks(cards)
    rank_counts: List[Tuple[int, int]] = []  # (count, rank_index)
    for r in range(13):
        c = ranks.count(r)
        if c > 0:
            rank_counts.append((c, r))
    rank_counts.sort(key=lambda x: (-x[0], -x[1]))  # count desc, then rank desc

    is_flush = _is_flush(cards)
    is_straight_bool, straight_high = _is_straight(ranks)

    # Straight flush
    if is_flush and is_straight_bool:
        return (HAND_RANK_STRAIGHT_FLUSH, (straight_high,))

    # Four of a kind
    if rank_counts[0][0] == 4:
        quad_rank = rank_counts[0][1]
        kicker = rank_counts[1][1]
        return (HAND_RANK_FOUR_OF_A_KIND, (quad_rank, kicker))

    # Full house
    if rank_counts[0][0] == 3 and rank_counts[1][0] >= 2:
        trip = rank_counts[0][1]
        pair = rank_counts[1][1]
        return (HAND_RANK_FULL_HOUSE, (trip, pair))

    # Flush
    if is_flush:
        high_ranks = sorted((-r for r in ranks), reverse=True)[:5]
        return (HAND_RANK_FLUSH, tuple(-r for r in high_ranks))

    # Straight
    if is_straight_bool:
        return (HAND_RANK_STRAIGHT, (straight_high,))

    # Three of a kind
    if rank_counts[0][0] == 3:
        trip = rank_counts[0][1]
        kickers = sorted((-rank_counts[i][1] for i in range(1, len(rank_counts))), reverse=True)[:2]
        return (HAND_RANK_THREE_OF_A_KIND, (trip,) + tuple(kickers))

    # Two pair
    if rank_counts[0][0] == 2 and rank_counts[1][0] == 2:
        p1, p2 = rank_counts[0][1], rank_counts[1][1]
        high_pair, low_pair = max(p1, p2), min(p1, p2)
        kicker = rank_counts[2][1] if len(rank_counts) > 2 else -1
        return (HAND_RANK_TWO_PAIR, (high_pair, low_pair, kicker))

    # Pair
    if rank_counts[0][0] == 2:
        pair_rank = rank_counts[0][1]
        kickers = sorted((-rank_counts[i][1] for i in range(1, len(rank_counts))), reverse=True)[:3]
        return (HAND_RANK_PAIR, (pair_rank,) + tuple(kickers))

    # High card
    high_ranks = sorted((-r for r in ranks), reverse=True)[:5]
    return (HAND_RANK_HIGH_CARD, tuple(-r for r in high_ranks))


def best_hand_from_cards(cards: List[Card]) -> Tuple[List[Card], int, Tuple[int, ...]]:
    """
    From 5-7 cards, return (best_5_cards, hand_type, tiebreaker).
    """
    if len(cards) < 5 or len(cards) > 7:
        raise ValueError("Need 5, 6, or 7 cards")
    if len(cards) == 5:
        hand_type, tiebreaker = _evaluate_five(cards)
        return (list(cards), hand_type, tiebreaker)
    best = None
    best_hand_type = -1
    best_tiebreaker: Tuple[int, ...] = ()
    for combo in combinations(cards, 5):
        hand_type, tiebreaker = _evaluate_five(list(combo))
        if hand_type > best_hand_type or (
            hand_type == best_hand_type and tiebreaker > best_tiebreaker
        ):
            best_hand_type = hand_type
            best_tiebreaker = tiebreaker
            best = list(combo)
    assert best is not None
    return (best, best_hand_type, best_tiebreaker)


def compare_hands(cards_a: List[Card], cards_b: List[Card]) -> int:
    """
    Compare two hands (each 5-7 cards). Return -1 if A wins, 1 if B wins, 0 if tie.
    """
    _, ta, tb_a = best_hand_from_cards(cards_a)
    _, tb, tb_b = best_hand_from_cards(cards_b)
    if ta != tb:
        return -1 if ta > tb else 1
    if tb_a > tb_b:
        return -1
    if tb_b > tb_a:
        return 1
    return 0


def hand_type_name(hand_type: int) -> str:
    names = [
        "high card", "pair", "two pair", "three of a kind", "straight",
        "flush", "full house", "four of a kind", "straight flush",
    ]
    return names[hand_type] if 0 <= hand_type <= 8 else "Unknown"
