"""Quick test of the game engine (run with: python -m engine.test_engine)."""
from .game_state import GameConfig, Player
from .game_controller import GameController

def main():
    config = GameConfig(small_blind=5, big_blind=10, starting_stack=500)
    players = [
        Player(id="alice", seat=0, stack=config.starting_stack, display_name="Alice"),
        Player(id="bob", seat=1, stack=config.starting_stack, display_name="Bob"),
    ]
    game = GameController(config=config, players=players)
    game.start_hand()
    state = game.get_state()
    print("Phase:", state["phase"])
    print("Pot:", state["pot"])
    print("Community:", state["community_cards"])
    for p in state["players"]:
        print("  ", p["id"], "stack=", p["stack"], "bet=", p["current_bet"], "cards=", p["hole_cards"])
    print("Current player seat:", state["current_player_seat"])
    print("Legal actions:", state["legal_actions"])
    print("Config blinds:", state["config"])
    print("OK")

if __name__ == "__main__":
    main()
