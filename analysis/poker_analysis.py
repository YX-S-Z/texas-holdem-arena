#!/usr/bin/env python3
"""
Texas Hold'em LLM Arena — Poker Analysis Pipeline
==================================================
Analyzes player behavior, strategy, and personality from game logs.

Usage:
    python analysis/poker_analysis.py                          # all games → analysis/output/
    python analysis/poker_analysis.py --game-dir data/<folder> # one game  → that folder/

Output:
    report.md               — Full personality + performance report
    metrics.csv             — Raw computed metrics per player
    figures/
        aggression_profile.png   — VPIP vs AF scatter (personality quadrants)
        performance_ranking.png  — Final chips & chips won ranking
        error_breakdown.png      — LLM output error counts by type
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("Warning: matplotlib not available — skipping charts. "
          "Install with: pip install matplotlib")

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR   = SCRIPT_DIR.parent / "data"
OUT_DIR    = SCRIPT_DIR / "output"
OUT_DIR.mkdir(exist_ok=True)

# ── Card / Chen Formula Utilities ──────────────────────────────────────────────
RANK_MAP = {
    "2": 2,  "3": 3,  "4": 4,  "5": 5,  "6": 6,  "7": 7,  "8": 8,
    "9": 9,  "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
}
CHEN_HIGH_CARD = {
    14: 10, 13: 8, 12: 7, 11: 6, 10: 5, 9: 4.5,
    8: 4,   7: 3.5, 6: 3, 5: 2.5, 4: 2, 3: 1.5, 2: 1,
}


def parse_hole_cards(card_str: str):
    """Parse hole cards string like 'S6 HK' → [(6,'S'), (13,'H')]. Returns None on failure."""
    if not isinstance(card_str, str) or not card_str.strip():
        return None
    parts = card_str.strip().split()
    if len(parts) != 2:
        return None
    cards = []
    for p in parts:
        if len(p) < 2:
            return None
        suit, rank_str = p[0], p[1:]
        if suit not in "SHCD" or rank_str not in RANK_MAP:
            return None
        cards.append((RANK_MAP[rank_str], suit))
    return cards


def chen_score(card_str: str) -> float:
    """
    Chen formula preflop hand strength (scale ~1–20).

    High card: A=10 K=8 Q=7 J=6 10=5 9=4.5 …
    Pair: score×2, min 5
    Suited: +2
    Gap penalties: 0-gap=0, 1=−1, 2=−2, 3=−4, 4+=−5
    Connector bonus: +1 if gap≤1 and lower rank < Q
    """
    cards = parse_hole_cards(card_str)
    if cards is None:
        return float("nan")
    r1, s1 = cards[0]
    r2, s2 = cards[1]
    high, low = max(r1, r2), min(r1, r2)
    score = CHEN_HIGH_CARD.get(high, 1.0)
    if r1 == r2:
        score = max(score * 2, 5.0)
    else:
        if s1 == s2:
            score += 2
        gap = high - low - 1
        if gap == 0:
            pass
        elif gap == 1:
            score -= 1
        elif gap == 2:
            score -= 2
        elif gap == 3:
            score -= 4
        else:
            score -= 5
        if gap <= 1 and low < 12:
            score += 1
    return round(score, 1)


# ── Data Loading ───────────────────────────────────────────────────────────────

def load_all_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load actions.csv + hands.csv from every game folder under DATA_DIR."""
    action_frames, hand_frames = [], []
    for folder in sorted(DATA_DIR.glob("*")):
        if not folder.is_dir():
            continue
        af, hf = folder / "actions.csv", folder / "hands.csv"
        if af.exists():
            try:
                df = pd.read_csv(af, dtype=str)
                df["_folder"] = folder.name
                action_frames.append(df)
            except Exception as e:
                print(f"  Warning: could not load {af}: {e}")
        if hf.exists():
            try:
                df = pd.read_csv(hf, dtype=str)
                df["_folder"] = folder.name
                hand_frames.append(df)
            except Exception as e:
                print(f"  Warning: could not load {hf}: {e}")
    if not action_frames:
        print("No game data found under", DATA_DIR)
        sys.exit(1)
    actions = pd.concat(action_frames, ignore_index=True)
    hands   = pd.concat(hand_frames,   ignore_index=True) if hand_frames else pd.DataFrame()
    return _coerce_and_score(actions, hands)


def load_game_dir(game_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load actions.csv + hands.csv from a single game folder."""
    action_frames, hand_frames = [], []
    af, hf = game_dir / "actions.csv", game_dir / "hands.csv"
    if af.exists():
        df = pd.read_csv(af, dtype=str)
        df["_folder"] = game_dir.name
        action_frames.append(df)
    if hf.exists():
        df = pd.read_csv(hf, dtype=str)
        df["_folder"] = game_dir.name
        hand_frames.append(df)
    if not action_frames:
        print(f"No actions.csv found in {game_dir}")
        sys.exit(1)
    actions = pd.concat(action_frames, ignore_index=True)
    hands   = pd.concat(hand_frames,   ignore_index=True) if hand_frames else pd.DataFrame()
    return _coerce_and_score(actions, hands)


def _coerce_and_score(actions: pd.DataFrame, hands: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Numeric coercions + Chen score pre-computation."""
    for col in ["hand_number", "pot", "stack", "current_bet", "action_amount"]:
        if col in actions.columns:
            actions[col] = pd.to_numeric(actions[col], errors="coerce")
    if not hands.empty:
        for col in ["hand_number", "pot"]:
            if col in hands.columns:
                hands[col] = pd.to_numeric(hands[col], errors="coerce")
    preflop_mask = actions["phase"] == "preflop"
    actions.loc[preflop_mask, "chen_score"] = (
        actions.loc[preflop_mask, "hole_cards"].apply(chen_score)
    )
    if "chen_score" not in actions.columns:
        actions["chen_score"] = float("nan")
    n_games = actions["game_id"].nunique()
    n_hands = len(actions[["game_id", "hand_number"]].drop_duplicates())
    print(f"  Loaded {len(actions)} actions | {n_games} game(s) | {n_hands} hand(s)")
    return actions, hands


# ── Metrics Computation ────────────────────────────────────────────────────────

AGGRESSIVE = {"raise", "bet"}
FOLD_SET   = {"fold"}


def _parse_winner_ids(ids_str: str) -> list[str]:
    return [w.strip() for w in str(ids_str).split("|") if w.strip()]


def _parse_amounts(amounts_str: str) -> list[float]:
    result = []
    for a in str(amounts_str).split("|"):
        try:
            result.append(float(a.strip()))
        except ValueError:
            pass
    return result


def compute_player_metrics(actions: pd.DataFrame, hands: pd.DataFrame) -> pd.DataFrame:
    """
    Compute per-player strategy and personality metrics.

    VPIP%        — Voluntarily Put $ In Pot (preflop call or raise %)
    PFR%         — Preflop Raise %
    AF           — Aggression Factor = (bets+raises) / calls, postflop
    AFq%         — Aggression Frequency = aggressive / non-fold actions
    fold_rate%   — Overall fold rate
    avg_raise    — Average postflop bet/raise size in chips
    error_rate%  — LLM output error rate (all types combined)
    final_chips  — Last recorded chip stack (proxy for end-of-game chips)
    chips_won    — Total chips won from pots (gross)
    """
    records = []

    for player_id in sorted(actions["player_id"].unique()):
        pa   = actions[actions["player_id"] == player_id]
        name = pa["display_name"].dropna().iloc[0] if len(pa) > 0 else player_id
        total = len(pa)

        # ── Action counts ─────────────────────────────────────────────────
        n_agg  = pa["action_type"].isin(AGGRESSIVE).sum()
        n_call = (pa["action_type"] == "call").sum()
        n_fold = (pa["action_type"] == "fold").sum()

        # ── Preflop ───────────────────────────────────────────────────────
        pre       = pa[pa["phase"] == "preflop"]
        pre_total = len(pre)
        pre_fold  = (pre["action_type"] == "fold").sum()
        hands_seen = pre["hand_number"].nunique()

        vpip_hands = (
            pre[pre["action_type"].isin(AGGRESSIVE) | (pre["action_type"] == "call")]
            ["hand_number"].nunique()
        )
        vpip_pct = vpip_hands / hands_seen * 100 if hands_seen > 0 else 0.0

        pfr_hands = pre[pre["action_type"].isin(AGGRESSIVE)]["hand_number"].nunique()
        pfr_pct   = pfr_hands / hands_seen * 100 if hands_seen > 0 else 0.0

        pre_fold_rate = pre_fold / pre_total * 100 if pre_total > 0 else 0.0

        # ── Postflop aggression ───────────────────────────────────────────
        post      = pa[pa["phase"] != "preflop"]
        post_agg  = post["action_type"].isin(AGGRESSIVE).sum()
        post_call = (post["action_type"] == "call").sum()

        af = (
            post_agg / post_call if post_call > 0
            else (float("inf") if post_agg > 0 else 0.0)
        )
        af_display = "∞" if af == float("inf") else round(af, 2)
        af_raw     = min(af, 10.0)

        non_fold = pa[~pa["action_type"].isin(FOLD_SET)]
        afq = n_agg / len(non_fold) * 100 if len(non_fold) > 0 else 0.0

        fold_rate = n_fold / total * 100 if total > 0 else 0.0

        # ── Bet/raise sizing ──────────────────────────────────────────────
        postflop_raises = post[post["action_type"].isin(AGGRESSIVE)]
        raise_amts = pd.to_numeric(
            postflop_raises["action_amount"], errors="coerce"
        ).dropna()
        avg_raise = raise_amts.mean() if len(raise_amts) > 0 else 0.0

        # ── LLM output error breakdown ────────────────────────────────────
        err = pa["failure_reason"].fillna("")
        n_parse_error         = (err == "parse_error").sum()
        n_parse_error_rescued = (err == "parse_error_rescued").sum()
        n_timeout             = (err == "timeout").sum()
        n_api_error           = (err == "api_error").sum()
        n_any_error           = pa["failure_reason"].notna().sum()
        error_rate = n_any_error / total * 100 if total > 0 else 0.0

        # ── Thinking depth ────────────────────────────────────────────────
        thinking_texts = pa["thinking"].dropna()
        avg_think_len  = thinking_texts.str.len().mean() if len(thinking_texts) > 0 else 0.0

        # ── Win / chips ───────────────────────────────────────────────────
        chips_won = 0.0
        hands_won = 0
        if not hands.empty and "winner_player_ids" in hands.columns:
            for _, hrow in hands.iterrows():
                winner_ids = _parse_winner_ids(hrow.get("winner_player_ids", ""))
                amounts    = _parse_amounts(hrow.get("winner_amounts", "0"))
                for i, wid in enumerate(winner_ids):
                    if wid == player_id:
                        hands_won += 1
                        chips_won += amounts[i] if i < len(amounts) else 0.0

        win_rate = hands_won / hands_seen * 100 if hands_seen > 0 else 0.0

        last_stack = pd.to_numeric(pa["stack"], errors="coerce").dropna()
        final_chips = int(last_stack.iloc[-1]) if len(last_stack) > 0 else 0

        records.append({
            "player_id":              player_id,
            "display_name":           name,
            "total_actions":          total,
            "hands_seen":             hands_seen,
            "vpip_pct":               round(vpip_pct, 1),
            "pfr_pct":                round(pfr_pct, 1),
            "af":                     af_display,
            "_af_raw":                af_raw,
            "afq":                    round(afq, 1),
            "fold_rate":              round(fold_rate, 1),
            "pre_fold_rate":          round(pre_fold_rate, 1),
            "avg_raise_size":         round(avg_raise, 1),
            "error_rate":             round(error_rate, 1),
            "n_parse_error":          int(n_parse_error),
            "n_parse_error_rescued":  int(n_parse_error_rescued),
            "n_timeout":              int(n_timeout),
            "n_api_error":            int(n_api_error),
            "n_any_error":            int(n_any_error),
            "avg_thinking_len":       round(avg_think_len, 0),
            "chips_won":              chips_won,
            "final_chips":            final_chips,
            "hands_won":              hands_won,
            "win_rate_pct":           round(win_rate, 1),
        })

    df = pd.DataFrame(records).set_index("player_id")
    return df


# ── Personality Classification ─────────────────────────────────────────────────
#
#   TAG  (Tight-Aggressive)  VPIP<22%, AF>1.2  — selective + aggressive
#   LAG  (Loose-Aggressive)  VPIP≥22%, AF>1.2  — wide range + aggressive
#   Nit  (Tight-Passive)     VPIP<22%, AF≤1.2  — very selective + passive
#   Fish (Loose-Passive)     VPIP≥22%, AF≤1.2  — calls too much, rarely raises

PERSONALITY_COLORS = {
    "TAG":  "#2196F3",
    "LAG":  "#F44336",
    "Nit":  "#9E9E9E",
    "Fish": "#FF9800",
}


def classify_personality(row) -> tuple[str, str]:
    vpip   = float(row.get("vpip_pct", 0) or 0)
    af_raw = float(row.get("_af_raw",  0) or 0)
    afq    = float(row.get("afq",      0) or 0)
    tight      = vpip < 22
    aggressive = af_raw > 1.2 or afq > 35
    if tight and aggressive:
        return "TAG", "Tight-Aggressive — selective hand entry combined with strong post-flop betting"
    elif tight and not aggressive:
        return "Nit", "Nit/Rock — rarely enters pots, very selective, passive when in hand"
    elif not tight and aggressive:
        return "LAG", "Loose-Aggressive — wide hand range with high aggression; unpredictable"
    else:
        return "Fish", "Calling Station — enters many pots but rarely raises; passive post-flop"


# ── Visualizations ─────────────────────────────────────────────────────────────

def plot_aggression_scatter(metrics: pd.DataFrame, out: Path):
    """VPIP vs AF scatter — classic player-type quadrant chart."""
    if not MATPLOTLIB_AVAILABLE:
        return

    fig, ax = plt.subplots(figsize=(9, 7))
    ax.axvspan(0,  22,  0, 1, alpha=0.04, color="#9E9E9E")
    ax.axvspan(22, 100, 0, 1, alpha=0.04, color="#FF9800")
    ax.axhline(1.2, color="#BDBDBD", linewidth=1, linestyle="--")
    ax.axvline(22,  color="#BDBDBD", linewidth=1, linestyle="--")

    ax.text(11, 0.15, "Nit/Rock", ha="center", color="#9E9E9E", fontsize=9, style="italic")
    ax.text(60, 0.15, "Fish / Calling Station", ha="center", color="#FF9800", fontsize=9, style="italic")
    ymax = max(5.5, metrics["_af_raw"].max() + 0.6)
    ax.text(11, ymax * 0.88, "TAG", ha="center", color="#2196F3", fontsize=10, fontweight="bold")
    ax.text(60, ymax * 0.88, "LAG", ha="center", color="#F44336", fontsize=10, fontweight="bold")

    for pid, row in metrics.iterrows():
        personality, _ = classify_personality(row)
        color = PERSONALITY_COLORS.get(personality, "#607D8B")
        x, y  = row["vpip_pct"], row["_af_raw"]
        ax.scatter(x, y, color=color, s=220, zorder=5, edgecolors="white", linewidth=2)
        ax.annotate(row["display_name"], (x, y),
                    textcoords="offset points", xytext=(10, 4),
                    fontsize=8.5, color="#333")

    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor=c,
               markersize=11, label=label)
        for label, c in PERSONALITY_COLORS.items()
    ]
    ax.legend(handles=legend_elements, loc="upper left", fontsize=9)
    ax.set_xlabel("VPIP % — Hand Entry Rate (preflop call/raise)", fontsize=11)
    ax.set_ylabel("Aggression Factor (postflop raises / calls)", fontsize=11)
    ax.set_title("Aggression Profile: VPIP vs AF", fontsize=14, fontweight="bold")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, ymax)
    plt.tight_layout()
    plt.savefig(out / "aggression_profile.png", dpi=150)
    plt.close()
    print("  ✓ aggression_profile.png")


def plot_performance_ranking(metrics: pd.DataFrame, out: Path):
    """
    Grouped horizontal bar chart: final chips (blue) and chips won (orange),
    sorted winner-first by final chip count.
    """
    if not MATPLOTLIB_AVAILABLE:
        return

    df     = metrics.sort_values("final_chips", ascending=False)
    y      = np.arange(len(df))
    height = 0.38

    fig, ax = plt.subplots(figsize=(12, max(4, len(df) * 0.8)))

    bars_final = ax.barh(y + height / 2, df["final_chips"], height=height,
                         color="#2196F3", label="Final chips", edgecolor="white")
    bars_won   = ax.barh(y - height / 2, df["chips_won"],   height=height,
                         color="#FF7043", label="Chips won from pots",
                         edgecolor="white", alpha=0.85)

    for bar, val in zip(bars_final, df["final_chips"]):
        ax.text(bar.get_width() + 15, bar.get_y() + bar.get_height() / 2,
                f"{int(val):,}", va="center", fontsize=8.5, fontweight="bold", color="#444")

    for bar, val in zip(bars_won, df["chips_won"]):
        ax.text(bar.get_width() + 15, bar.get_y() + bar.get_height() / 2,
                f"{int(val):,}", va="center", fontsize=8, color="#444")

    ax.set_yticks(y)
    ax.set_yticklabels(df["display_name"].tolist(), fontsize=9)
    ax.set_xlabel("Chips", fontsize=11)
    ax.set_title(
        "Performance Ranking — Final Chips & Chips Won\n(winner first)",
        fontsize=13, fontweight="bold")
    ax.legend(loc="lower right", fontsize=9)
    xmax = max(df["final_chips"].max(), df["chips_won"].max()) * 1.2
    ax.set_xlim(0, max(xmax, 100))
    ax.invert_yaxis()
    plt.tight_layout()
    plt.savefig(out / "performance_ranking.png", dpi=150)
    plt.close()
    print("  ✓ performance_ranking.png")


def plot_error_breakdown(metrics: pd.DataFrame, out: Path):
    """
    Stacked horizontal bar chart: error type counts per player.
    Only players with at least one error are shown.
    """
    if not MATPLOTLIB_AVAILABLE:
        return

    error_cols   = ["n_parse_error", "n_parse_error_rescued", "n_timeout", "n_api_error"]
    error_labels = ["Parse Error (fallback)", "Parse Rescued (guardrail)", "Timeout", "API Error"]
    error_colors = ["#EF5350", "#FF9800", "#9C27B0", "#607D8B"]

    has_errors = metrics[error_cols].sum(axis=1) > 0
    df = metrics[has_errors].copy()

    if df.empty:
        print("  ⚠ Skipping error_breakdown (no errors recorded)")
        return

    names  = df["display_name"].tolist()
    totals = df["total_actions"].tolist()

    fig, ax = plt.subplots(figsize=(12, max(3, len(df) * 0.7)))
    left = np.zeros(len(df))

    for col, label, color in zip(error_cols, error_labels, error_colors):
        vals = df[col].values.astype(float)
        ax.barh(names, vals, left=left, color=color, label=label, height=0.55)
        for i, (v, l) in enumerate(zip(vals, left)):
            if v >= 1:
                ax.text(l + v / 2, i, str(int(v)), ha="center", va="center",
                        fontsize=8, color="#444", fontweight="bold")
        left += vals

    for i, (total_err, total_act) in enumerate(zip(left, totals)):
        rate = total_err / total_act * 100 if total_act > 0 else 0
        ax.text(left[i] + 0.2, i, f"  {rate:.0f}% of {int(total_act)} moves",
                va="center", fontsize=8, color="#444")

    ax.set_xlabel("Number of Errors", fontsize=11)
    ax.set_title("LLM Output Error Breakdown by Player", fontsize=14, fontweight="bold")
    ax.legend(loc="lower right", fontsize=9)
    ax.invert_yaxis()
    plt.tight_layout()
    plt.savefig(out / "error_breakdown.png", dpi=150)
    plt.close()
    print("  ✓ error_breakdown.png")


# ── Report Generation ──────────────────────────────────────────────────────────

def generate_report(
    metrics: pd.DataFrame,
    actions: pd.DataFrame,
    hands: pd.DataFrame,
) -> str:
    now       = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_games   = actions["game_id"].nunique()
    n_hands   = len(actions[["game_id", "hand_number"]].drop_duplicates())
    n_actions = len(actions)
    n_players = len(metrics)
    ranked    = metrics.sort_values("final_chips", ascending=False)

    lines = [
        "# Texas Hold'em LLM Arena — Analysis Report",
        f"*Generated: {now}*",
        "",
        "---",
        "",
        "## Overview",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Games analyzed | {n_games} |",
        f"| Hands played | {n_hands} |",
        f"| Players | {n_players} |",
        f"| Total actions logged | {n_actions} |",
        "",
        "---",
        "",
        "## Performance Ranking",
        "",
        "Sorted by **final chip count** (winner first). "
        "Final chips = stack at last recorded action. "
        "Chips won = gross pot winnings.",
        "",
        "| Rank | Player | Final Chips | Chips Won | Hands Won | Win Rate | Archetype |",
        "|------|--------|-------------|-----------|-----------|----------|-----------|",
    ]

    for rank, (pid, row) in enumerate(ranked.iterrows(), 1):
        personality, _ = classify_personality(row)
        lines.append(
            f"| {rank} | **{row['display_name']}** | {int(row['final_chips']):,} | "
            f"{row['chips_won']:,.0f} | {row['hands_won']} | "
            f"{row['win_rate_pct']}% | {personality} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Strategy Metrics",
        "",
        "| Player | VPIP% | PFR% | AF | AFq% | Fold% | Avg Raise | Think Len |",
        "|--------|-------|------|----|------|-------|-----------|-----------|",
    ]

    for pid, row in metrics.iterrows():
        lines.append(
            f"| {row['display_name']} | {row['vpip_pct']} | {row['pfr_pct']} | "
            f"{row['af']} | {row['afq']} | {row['fold_rate']} | "
            f"{row['avg_raise_size']} | {row['avg_thinking_len']:.0f} |"
        )

    lines += [
        "",
        "**Metric glossary:**",
        "",
        "| Metric | Definition |",
        "|--------|------------|",
        "| **VPIP%** | Voluntarily Put $ In Pot — % of hands with a preflop call or raise |",
        "| **PFR%**  | Preflop Raise % |",
        "| **AF**    | Aggression Factor = (bets+raises) ÷ calls, postflop; >1.5 = aggressive |",
        "| **AFq%**  | Aggression Frequency — % of non-fold actions that are bets/raises |",
        "| **Fold%** | Overall fold rate |",
        "| **Avg Raise** | Mean bet/raise size in chips (postflop only) |",
        "| **Think Len** | Mean length of `thinking` reasoning text in characters |",
        "",
        "---",
        "",
        "## LLM Output Reliability",
        "",
        "Tracks how often each model's output failed to parse correctly. "
        "High error rates indicate poor instruction-following for structured JSON output.",
        "",
        "| Player | Total Moves | Parse Error | Rescued | Timeout | API Error | Error Rate |",
        "|--------|-------------|-------------|---------|---------|-----------|------------|",
    ]

    for pid, row in metrics.sort_values("error_rate", ascending=False).iterrows():
        rate_str = f"**{row['error_rate']:.1f}%**" if row['error_rate'] >= 20 else f"{row['error_rate']:.1f}%"
        lines.append(
            f"| {row['display_name']} | {int(row['total_actions'])} | "
            f"{int(row['n_parse_error'])} | {int(row['n_parse_error_rescued'])} | "
            f"{int(row['n_timeout'])} | {int(row['n_api_error'])} | {rate_str} |"
        )

    lines += [
        "",
        "**Error type glossary:**",
        "",
        "| Type | Meaning |",
        "|------|---------|",
        "| **Parse Error** | Model output could not be parsed; fallback action (fold/check) used |",
        "| **Rescued** | Primary parse failed but guardrail extracted a valid action |",
        "| **Timeout** | Model API call exceeded the time limit; fallback used |",
        "| **API Error** | Network or upstream API failure |",
        "",
        "---",
        "",
        "## Personality Profiles",
        "",
        "Classified into four canonical poker archetypes using VPIP% and Aggression Factor:",
        "",
        "| Archetype | VPIP | AF | Play Style |",
        "|-----------|------|----|------------|",
        "| **TAG** (Tight-Aggressive) | <22% | >1.2 | Patient entry, strong betting |",
        "| **LAG** (Loose-Aggressive) | ≥22% | >1.2 | Wide range + aggression; high-variance |",
        "| **Nit/Rock**               | <22% | ≤1.2 | Overly selective; passive when in hand |",
        "| **Fish** (Calling Station) | ≥22% | ≤1.2 | Calls too much, rarely raises |",
        "",
    ]

    for pid, row in metrics.iterrows():
        personality, description = classify_personality(row)
        lines += [
            f"### {row['display_name']}  `[{personality}]`",
            "",
            f"> _{description}_",
            "",
            "**Key stats:**",
            "",
            f"- VPIP **{row['vpip_pct']}%** | PFR **{row['pfr_pct']}%** | "
            f"AF **{row['af']}** | AFq **{row['afq']}%**",
            f"- Overall fold rate: **{row['fold_rate']}%** | "
            f"Preflop fold rate: **{row['pre_fold_rate']}%**",
            f"- Avg postflop raise: **{row['avg_raise_size']} chips** | "
            f"Avg reasoning length: **{row['avg_thinking_len']:.0f} chars**",
            f"- LLM error rate: **{row['error_rate']}%** "
            f"({int(row['n_parse_error'])} parse, "
            f"{int(row['n_parse_error_rescued'])} rescued, "
            f"{int(row['n_timeout'])} timeout, "
            f"{int(row['n_api_error'])} API)",
            "",
        ]

    lines += [
        "---",
        "",
        "## Figures",
        "",
        "All charts are saved to `figures/`:",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `aggression_profile.png` | VPIP vs AF scatter with personality quadrants |",
        "| `performance_ranking.png` | Final chips & chips won, sorted winner-first |",
        "| `error_breakdown.png` | LLM output error counts by type per player |",
        "",
        "---",
        "",
        "*Analysis powered by Texas Hold'em LLM Arena — "
        "metrics based on standard poker strategy theory (VPIP, PFR, AF, Chen formula).*",
    ]

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Texas Hold'em LLM Arena — Poker Analysis Pipeline"
    )
    parser.add_argument(
        "--game-dir",
        metavar="PATH",
        help=(
            "Path to a single game folder (e.g. data/20260218_225446_game_0). "
            "Output is written into that folder. "
            "Omit to analyse ALL game folders under data/ → analysis/output/."
        ),
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Texas Hold'em LLM Arena — Poker Analysis Pipeline")
    print("=" * 60)
    print()

    if args.game_dir:
        game_dir = Path(args.game_dir)
        if not game_dir.is_dir():
            print(f"Error: {game_dir} is not a directory.")
            sys.exit(1)
        out_dir = game_dir
        print(f"► Loading data from {game_dir} ...")
        actions, hands = load_game_dir(game_dir)
    else:
        out_dir = OUT_DIR
        print("► Loading all game data...")
        actions, hands = load_all_data()

    out_dir.mkdir(exist_ok=True)
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(exist_ok=True)

    print("► Computing player metrics...")
    metrics = compute_player_metrics(actions, hands)
    metrics["personality"] = metrics.apply(lambda r: classify_personality(r)[0], axis=1)

    print()
    if MATPLOTLIB_AVAILABLE:
        print(f"► Generating charts → {fig_dir}")
        plot_aggression_scatter(metrics, fig_dir)
        plot_performance_ranking(metrics, fig_dir)
        plot_error_breakdown(metrics, fig_dir)
    else:
        print("► Skipping charts (matplotlib not installed)")

    print()
    print("► Writing report and metrics CSV...")
    report_text = generate_report(metrics, actions, hands)
    (out_dir / "report.md").write_text(report_text, encoding="utf-8")
    print("  ✓ report.md")

    save_cols = [c for c in metrics.columns if not c.startswith("_")]
    metrics[save_cols].to_csv(out_dir / "metrics.csv")
    print("  ✓ metrics.csv")

    print()
    print("=" * 60)
    print(f"  Done!  Output → {out_dir}")
    print("=" * 60)
    print()

    print("PERFORMANCE RANKING")
    print(f"  {'Rank':<5} {'Player':<22} {'Final Chips':>12} {'Chips Won':>10} {'W/L':>6}  Style")
    print(f"  {'-'*5} {'-'*22} {'-'*12} {'-'*10} {'-'*6}  -----")
    ranked = metrics.sort_values("final_chips", ascending=False)
    for i, (pid, row) in enumerate(ranked.iterrows(), 1):
        wl = f"{row['hands_won']}/{int(row['hands_seen'])}"
        print(f"  {i:<5} {row['display_name']:<22} {int(row['final_chips']):>12,} "
              f"{row['chips_won']:>10,.0f} {wl:>6}  [{row['personality']}]")
    print()

if __name__ == "__main__":
    main()
