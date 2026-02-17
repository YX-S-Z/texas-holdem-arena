#!/usr/bin/env python3
"""Download card images from RL4VLM gym-cards to static/img/cards/."""
import os
import ssl
import urllib.request

BASE = "https://raw.githubusercontent.com/RL4VLM/RL4VLM/main/gym-cards/gym_cards/envs/img"
SUITS = "CDHS"  # Clubs, Diamonds, Hearts, Spades
RANKS = "23456789TJQKA"

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(script_dir, "..", "static", "img", "cards")
    os.makedirs(out_dir, exist_ok=True)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    urllib.request.install_opener(opener)
    for s in SUITS:
        for r in RANKS:
            code = s + r
            url = f"{BASE}/{code}.png"
            path = os.path.join(out_dir, f"{code}.png")
            try:
                urllib.request.urlretrieve(url, path)
                print(f"  {code}.png")
            except Exception as e:
                print(f"  {code}.png FAILED: {e}")
    try:
        urllib.request.urlretrieve(f"{BASE}/card.png", os.path.join(out_dir, "card.png"))
        print("  card.png")
    except Exception as e:
        print(f"  card.png FAILED: {e}")
    print(f"Done. Images in {os.path.abspath(out_dir)}")

if __name__ == "__main__":
    main()
