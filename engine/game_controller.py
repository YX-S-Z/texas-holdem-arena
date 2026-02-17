"""
Texas Hold'em game controller: one object per game.
Handles deal, betting rounds, showdown, and exposes get_state() / apply_action().
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .cards import Card, make_deck, shuffle_deck
from .game_state import (
    GameConfig,
    PHASE_FLOP,
    PHASE_HAND_OVER,
    PHASE_PREFLOP,
    PHASE_RIVER,
    PHASE_SHOWDOWN,
    PHASE_TURN,
    PHASE_WAITING,
    Player,
)
from .hand_evaluator import best_hand_from_cards, compare_hands


@dataclass
class GameController:
    """Runs one Texas Hold'em table. Customize via config (e.g. blinds)."""
    config: GameConfig
    players: List[Player] = field(default_factory=list)

    # Hand state
    _deck: List[Card] = field(default_factory=list)
    _community_cards: List[Card] = field(default_factory=list)
    _pot: int = 0
    _current_bet_this_round: int = 0
    _dealer_seat: int = 0
    _phase: str = PHASE_WAITING
    _current_player_seat: Optional[int] = None
    _has_acted_this_round: Set[int] = field(default_factory=set)
    _winners: List[Dict[str, Any]] = field(default_factory=list)
    _hand_finished: bool = False

    def _player_by_seat(self, seat: int) -> Optional[Player]:
        for p in self.players:
            if p.seat == seat:
                return p
        return None

    def _player_by_id(self, player_id: str) -> Optional[Player]:
        for p in self.players:
            if p.id == player_id:
                return p
        return None

    def _active_players(self) -> List[Player]:
        return [p for p in self.players if not p.folded]

    def _players_in_hand_order(self, first_seat: int) -> List[Player]:
        """Return players in turn order starting from first_seat (by seat index)."""
        seats = sorted(p.seat for p in self.players)
        n = len(seats)
        start_idx = next(
            (i for i in range(n) if seats[i] >= first_seat),
            0,
        )
        order: List[Player] = []
        for i in range(n):
            seat = seats[(start_idx + i) % n]
            p = self._player_by_seat(seat)
            if p is not None:
                order.append(p)
        return order

    def _is_betting_round_complete(self) -> bool:
        """True when every non-folded player has acted since the last raise
        and matched the current bet (or is all-in)."""
        for p in self.players:
            if p.folded:
                continue
            if p.stack == 0:
                continue  # all-in, cannot act further
            if p.seat not in self._has_acted_this_round:
                return False  # hasn't had a turn since last raise / round start
            if p.current_bet != self._current_bet_this_round:
                return False  # hasn't matched the bet
        return True

    def _advance_to_next_player(self) -> bool:
        """Move to next active player who can still bet.
        Returns True when the betting round is complete."""
        if self._is_betting_round_complete():
            return True
        if self._current_player_seat is None:
            return True
        seats = sorted(p.seat for p in self.players)
        n = len(seats)
        cur_idx = seats.index(self._current_player_seat)
        for step in range(1, n + 1):
            candidate_seat = seats[(cur_idx + step) % n]
            candidate = self._player_by_seat(candidate_seat)
            if candidate is None or candidate.folded or candidate.stack == 0:
                continue
            self._current_player_seat = candidate.seat
            return False
        return True  # no eligible player found

    def _run_showdown(self) -> None:
        """Set winners and pay pot(s). Single pot only for v1."""
        active = self._active_players()
        if not active:
            return
        if len(active) == 1:
            winner = active[0]
            winner.stack += self._pot
            self._winners = [{"player_id": winner.id, "amount": self._pot}]
            self._pot = 0
            self._phase = PHASE_HAND_OVER
            self._hand_finished = True
            return
        all_cards = [p.hole_cards + self._community_cards for p in active]
        best_idx = 0
        for i in range(1, len(active)):
            if compare_hands(all_cards[best_idx], all_cards[i]) == 1:
                best_idx = i
        winners = [active[best_idx]]
        for i in range(len(active)):
            if i != best_idx and compare_hands(all_cards[best_idx], all_cards[i]) == 0:
                winners.append(active[i])
        split = self._pot // len(winners)
        remainder = self._pot % len(winners)
        for i, p in enumerate(winners):
            p.stack += split + (1 if i < remainder else 0)
        self._winners = [
            {"player_id": p.id, "amount": split + (1 if i < remainder else 0)}
            for i, p in enumerate(winners)
        ]
        self._pot = 0
        self._phase = PHASE_HAND_OVER
        self._hand_finished = True

    def _post_blinds(self) -> None:
        """Post small and big blind. First active after dealer is SB, next is BB."""
        order = self._players_in_hand_order(self._dealer_seat + 1)
        order = [p for p in order if p.stack > 0]
        if len(order) < 2:
            return
        sb_player = order[0]
        bb_player = order[1]
        sb_amt = min(self.config.small_blind, sb_player.stack)
        bb_amt = min(self.config.big_blind, bb_player.stack)
        sb_player.stack -= sb_amt
        bb_player.stack -= bb_amt
        sb_player.current_bet = sb_amt
        bb_player.current_bet = bb_amt
        self._pot = sb_amt + bb_amt
        self._current_bet_this_round = bb_amt
        # Nobody has voluntarily acted yet; blinds are forced
        self._has_acted_this_round = set()
        # First to act preflop is left of big blind (UTG); heads-up it is SB
        first_act = order[2] if len(order) > 2 else order[0]
        self._current_player_seat = first_act.seat

    def _deal_community(self, count: int) -> None:
        for _ in range(count):
            if self._deck:
                self._community_cards.append(self._deck.pop())

    def _advance_street(self) -> None:
        """After betting round: deal next community cards or showdown."""
        if self._phase == PHASE_PREFLOP:
            self._deal_community(3)
            self._phase = PHASE_FLOP
        elif self._phase == PHASE_FLOP:
            self._deal_community(1)
            self._phase = PHASE_TURN
        elif self._phase == PHASE_TURN:
            self._deal_community(1)
            self._phase = PHASE_RIVER
        elif self._phase == PHASE_RIVER:
            self._phase = PHASE_SHOWDOWN
            self._run_showdown()
            return
        # Reset per-round state
        self._current_bet_this_round = 0
        self._has_acted_this_round = set()
        for p in self.players:
            p.current_bet = 0
        # First to act post-flop is first active player after dealer
        order = self._players_in_hand_order(self._dealer_seat + 1)
        order = [p for p in order if not p.folded and p.stack > 0]
        if order:
            self._current_player_seat = order[0].seat
        else:
            self._current_player_seat = None
            # Everyone is all-in; deal remaining streets
            self._advance_street()

    def start_hand(self) -> None:
        """Start a new hand: shuffle, post blinds, deal hole cards, set preflop."""
        if len(self.players) < self.config.min_players:
            raise ValueError(f"Need at least {self.config.min_players} players")
        self._deck = shuffle_deck(make_deck())
        self._community_cards = []
        self._pot = 0
        self._current_bet_this_round = 0
        self._has_acted_this_round = set()
        self._winners = []
        self._hand_finished = False
        for p in self.players:
            p.folded = False
            p.hole_cards = []
            p.current_bet = 0
        # Dealer button: advance for next hand (round-robin)
        seats = sorted(p.seat for p in self.players)
        self._dealer_seat = seats[(seats.index(self._dealer_seat) + 1) % len(seats)] if self._phase != PHASE_WAITING else seats[0]
        # Deal 2 cards each
        order = self._players_in_hand_order(self._dealer_seat + 1)
        for _ in range(2):
            for p in order:
                if self._deck:
                    p.hole_cards.append(self._deck.pop())
        self._phase = PHASE_PREFLOP
        self._post_blinds()
        if self._current_player_seat is None:
            self._advance_street()

    def get_legal_actions(self, player_id: str) -> List[Dict[str, Any]]:
        """Return list of {type, amount?} for the current player."""
        p = self._player_by_id(player_id)
        if p is None or p.folded or self._phase in (PHASE_SHOWDOWN, PHASE_HAND_OVER):
            return []
        if self._current_player_seat != p.seat:
            return []
        if p.stack == 0:
            return []  # all-in, no actions available
        actions: List[Dict[str, Any]] = []
        to_call = self._current_bet_this_round - p.current_bet
        min_raise = self.config.raise_min
        actions.append({"type": "fold"})
        if to_call == 0:
            actions.append({"type": "check"})
        if to_call > 0 and p.stack > 0:
            call_amt = min(to_call, p.stack)
            actions.append({"type": "call", "amount": call_amt})
        if p.stack > to_call:
            raise_min_amt = self._current_bet_this_round + min_raise - p.current_bet
            if p.stack >= raise_min_amt:
                actions.append({"type": "raise", "min_amount": raise_min_amt, "max_amount": p.stack})
        return actions

    def apply_action(self, player_id: str, action: Dict[str, Any]) -> None:
        """Apply fold/check/call/raise. Raises ValueError if invalid."""
        p = self._player_by_id(player_id)
        if p is None:
            raise ValueError("Unknown player")
        if p.folded or self._phase in (PHASE_SHOWDOWN, PHASE_HAND_OVER):
            raise ValueError("Cannot act now")
        if self._current_player_seat != p.seat:
            raise ValueError("Not your turn")
        action_type = action.get("type")
        if action_type == "fold":
            p.folded = True
        elif action_type == "check":
            if self._current_bet_this_round != p.current_bet:
                raise ValueError("Cannot check")
        elif action_type == "call":
            to_call = self._current_bet_this_round - p.current_bet
            if to_call <= 0:
                raise ValueError("Invalid call")
            amt = min(to_call, p.stack)
            p.stack -= amt
            p.current_bet += amt
            self._pot += amt
        elif action_type == "raise":
            amount = action.get("amount")
            if amount is None:
                raise ValueError("Raise requires amount")
            min_raise_amt = self._current_bet_this_round + self.config.raise_min - p.current_bet
            if amount < min_raise_amt or amount > p.stack:
                raise ValueError("Invalid raise amount")
            p.stack -= amount
            p.current_bet += amount
            self._pot += amount
            self._current_bet_this_round = p.current_bet
            # A raise reopens betting: only the raiser has "acted" at this new level
            self._has_acted_this_round = set()
        else:
            raise ValueError(f"Unknown action: {action_type}")
        # Record that this player has acted
        self._has_acted_this_round.add(p.seat)
        # Check: only one active player left -> immediate win
        if action_type == "fold" and len(self._active_players()) == 1:
            self._run_showdown()
            return
        # Advance to next player, or next street if round is complete
        round_complete = self._advance_to_next_player()
        if round_complete:
            self._advance_street()

    def get_state(self, viewer_id: Optional[str] = None) -> Dict[str, Any]:
        """Serializable state for API. Partial visibility: pass the player who is viewing.

        - When viewer_id is set, only that player's hole_cards are included; others get [].
        - At showdown/hand_over, all players' hole cards are shown.
        """
        show_down = self._phase in (PHASE_SHOWDOWN, PHASE_HAND_OVER)
        players_out = [
            p.to_public_dict(show_hole_cards=(show_down or (viewer_id is not None and p.id == viewer_id)))
            for p in sorted(self.players, key=lambda x: x.seat)
        ]
        current_player = self._player_by_seat(self._current_player_seat) if self._current_player_seat is not None else None
        legal_actions = self.get_legal_actions(current_player.id) if current_player is not None else []
        out: Dict[str, Any] = {
            "viewer_id": viewer_id,
            "phase": self._phase,
            "players": players_out,
            "community_cards": [c.code for c in self._community_cards],
            "pot": self._pot,
            "current_bet_this_round": self._current_bet_this_round,
            "dealer_seat": self._dealer_seat,
            "current_player_seat": self._current_player_seat,
            "current_player_id": current_player.id if current_player is not None else None,
            "legal_actions": legal_actions,
            "winners": self._winners,
            "config": {
                "small_blind": self.config.small_blind,
                "big_blind": self.config.big_blind,
            },
        }
        return out
