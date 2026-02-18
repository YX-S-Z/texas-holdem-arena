#!/usr/bin/env python3
"""
Texas Hold'em LLM Arena — Poker Analysis Pipeline
==================================================
Analyzes player behavior, strategy, and personality from game logs.

Usage:
    python analysis/poker_analysis.py

Output (in analysis/output/):
    report.md               — Full personality + performance report
    metrics.csv             — Raw computed metrics per player
    action_distribution.png — Stacked action breakdown per player
    aggression_profile.png  — VPIP vs AF scatter (personality quadrants)
    performance_ranking.png — Net chips ranking
    chen_vs_action.png      — Preflop hand strength (Chen) by action type
    personality_radar.png   — Multi-dimensional radar chart
    thinking_keywords.png   — Thinking log keyword density analysis
"""

from __future__ import annotations

import os
import sys
import math
import re
from collections import defaultdict
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
    print("Warning: matplotlib not available — skipping charts. Install with: pip install matplotlib")

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR   = SCRIPT_DIR.parent / "data"
OUT_DIR    = SCRIPT_DIR / "output"
OUT_DIR.mkdir(exist_ok=True)

# ── Card / Chen Formula Utilities ─────────────────────────────────────────────
RANK_MAP = {
    "2": 2,  "3": 3,  "4": 4,  "5": 5,  "6": 6,  "7": 7,  "8": 8,
    "9": 9,  "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
}
CHEN_HIGH_CARD = {
    14: 10, 13: 8, 12: 7, 11: 6, 10: 5, 9: 4.5,
    8: 4,   7: 3.5, 6: 3, 5: 2.5, 4: 2, 3: 1.5, 2: 1,
}


def parse_hole_cards(card_str: str):
    """
    Parse hole cards string like 'S6 HK' → [(6,'S'), (13,'H')].
    Format: suit char first, then rank char(s). e.g. 'S6'=6♠, 'DA'=A♦.
    Returns None on any parse failure.
    """
    if not isinstance(card_str, str) or not card_str.strip():
        return None
    parts = card_str.strip().split()
    if len(parts) != 2:
        return None
    cards = []
    for p in parts:
        if len(p) < 2:
            return None
        suit = p[0]
        rank_str = p[1:]
        if suit not in "SHCD" or rank_str not in RANK_MAP:
            return None
        cards.append((RANK_MAP[rank_str], suit))
    return cards


def chen_score(card_str: str) -> float:
    """
    Chen formula preflop hand strength score (scale ~1–20).
    Used to measure hand quality when a player voluntarily enters the pot.

    Scoring:
      High card:   A=10, K=8, Q=7, J=6, 10=5, 9=4.5, 8=4, 7=3.5, 6=3, 5=2.5, 4=2, 3=1.5, 2=1
      Pair:        score×2, minimum 5
      Suited:      +2
      Gap penalty: connected=0, 1-gap=−1, 2-gap=−2, 3-gap=−4, 4+gap=−5
      Straight bonus: +1 if gap≤1 and lower card rank < Q
    """
    cards = parse_hole_cards(card_str)
    if cards is None:
        return float("nan")

    r1, s1 = cards[0]
    r2, s2 = cards[1]
    high, low = max(r1, r2), min(r1, r2)

    score = CHEN_HIGH_CARD.get(high, 1.0)

    if r1 == r2:
        # Pair: double and floor at 5
        score = max(score * 2, 5.0)
    else:
        if s1 == s2:
            score += 2  # Suited bonus

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

        # Connector straight bonus (not for very high connectors near A)
        if gap <= 1 and low < 12:
            score += 1

    return round(score, 1)


# ── Data Loading ──────────────────────────────────────────────────────────────

def load_all_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load all actions.csv and hands.csv from every game folder under DATA_DIR.
    Returns (actions_df, hands_df).
    """
    action_frames, hand_frames = [], []

    for folder in sorted(DATA_DIR.glob("*")):
        if not folder.is_dir():
            continue
        af = folder / "actions.csv"
        hf = folder / "hands.csv"

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
        print("Run some games first, then re-run this script.")
        sys.exit(1)

    actions = pd.concat(action_frames, ignore_index=True)
    hands   = pd.concat(hand_frames, ignore_index=True) if hand_frames else pd.DataFrame()
    return _coerce_and_score(actions, hands)


# ── Metrics Computation ───────────────────────────────────────────────────────

AGGRESSIVE = {"raise", "bet"}
PASSIVE    = {"call", "check"}
FOLD_SET   = {"fold"}


def _parse_winner_ids(ids_str: str) -> list[str]:
    """Split '|'-delimited winner id string into a list."""
    return [w.strip() for w in str(ids_str).split("|") if w.strip()]


def _parse_amounts(amounts_str: str) -> list[float]:
    """Split '|'-delimited amounts string into floats."""
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

    Key metrics:
      VPIP%       — Voluntarily Put $ In Pot (preflop call or raise %)
      PFR%        — Preflop Raise %
      AF          — Aggression Factor = (bets+raises) / calls (postflop)
      AFq%        — Aggression Frequency = aggressive / non-fold actions
      fold_rate%  — Overall fold rate
      avg_raise   — Average bet/raise size (postflop, in chips)
      error_rate% — LLM parse-error rate (output formatting failures)
      chen_enter  — Average Chen hand score when voluntarily entering pot
      chen_fold   — Average Chen hand score when folding preflop
      chips_won   — Total chips won from pots
    """
    records = []

    for player_id in sorted(actions["player_id"].unique()):
        pa   = actions[actions["player_id"] == player_id]
        name = pa["display_name"].dropna().iloc[0] if len(pa) > 0 else player_id

        total = len(pa)

        # ── Action counts ──────────────────────────────────────────────────
        n_agg   = pa["action_type"].isin(AGGRESSIVE).sum()
        n_call  = (pa["action_type"] == "call").sum()
        n_check = (pa["action_type"] == "check").sum()
        n_fold  = (pa["action_type"] == "fold").sum()

        # ── Preflop subset ─────────────────────────────────────────────────
        pre        = pa[pa["phase"] == "preflop"]
        pre_total  = len(pre)
        pre_agg    = pre["action_type"].isin(AGGRESSIVE).sum()
        pre_call   = (pre["action_type"] == "call").sum()
        pre_fold   = (pre["action_type"] == "fold").sum()

        # Unique hands where player had a preflop decision
        hands_seen = pre["hand_number"].nunique()

        # VPIP: unique hands where player called or raised preflop
        vpip_hands = (
            pre[pre["action_type"].isin(AGGRESSIVE) | (pre["action_type"] == "call")]
            ["hand_number"].nunique()
        )
        vpip_pct = vpip_hands / hands_seen * 100 if hands_seen > 0 else 0.0

        # PFR: unique hands where player raised preflop
        pfr_hands = (
            pre[pre["action_type"].isin(AGGRESSIVE)]["hand_number"].nunique()
        )
        pfr_pct = pfr_hands / hands_seen * 100 if hands_seen > 0 else 0.0

        # Preflop fold rate
        pre_fold_rate = pre_fold / pre_total * 100 if pre_total > 0 else 0.0

        # ── Postflop aggression ────────────────────────────────────────────
        post      = pa[pa["phase"] != "preflop"]
        post_agg  = post["action_type"].isin(AGGRESSIVE).sum()
        post_call = (post["action_type"] == "call").sum()

        af = (
            post_agg / post_call if post_call > 0
            else (float("inf") if post_agg > 0 else 0.0)
        )
        af_display = "∞" if af == float("inf") else round(af, 2)
        af_raw     = min(af, 10.0)  # cap at 10 for charting

        # AFq: aggressive / non-fold actions
        non_fold = pa[~pa["action_type"].isin(FOLD_SET)]
        afq = n_agg / len(non_fold) * 100 if len(non_fold) > 0 else 0.0

        # Overall fold rate
        fold_rate = n_fold / total * 100 if total > 0 else 0.0

        # ── Bet/raise sizing ───────────────────────────────────────────────
        postflop_raises = post[post["action_type"].isin(AGGRESSIVE)]
        raise_amts = pd.to_numeric(
            postflop_raises["action_amount"], errors="coerce"
        ).dropna()
        avg_raise = raise_amts.mean() if len(raise_amts) > 0 else 0.0

        # ── LLM quality — error type breakdown ────────────────────────────
        err = pa["failure_reason"].fillna("")
        n_parse_error          = (err == "parse_error").sum()
        n_parse_error_rescued  = (err == "parse_error_rescued").sum()
        n_timeout              = (err == "timeout").sum()
        n_api_error            = (err == "api_error").sum()
        n_any_error            = pa["failure_reason"].notna().sum()
        error_rate = n_any_error / total * 100 if total > 0 else 0.0

        # ── Thinking depth ────────────────────────────────────────────────
        thinking_texts = pa["thinking"].dropna()
        avg_think_len  = thinking_texts.str.len().mean() if len(thinking_texts) > 0 else 0.0

        # ── Preflop hand quality ───────────────────────────────────────────
        enter_mask = (
            pa["phase"] == "preflop"
        ) & (
            pa["action_type"].isin(AGGRESSIVE) | (pa["action_type"] == "call")
        )
        fold_mask = (pa["phase"] == "preflop") & pa["action_type"].isin(FOLD_SET)

        chen_enter_scores = pd.to_numeric(
            pa.loc[enter_mask, "chen_score"], errors="coerce"
        ).dropna()
        chen_fold_scores = pd.to_numeric(
            pa.loc[fold_mask, "chen_score"], errors="coerce"
        ).dropna()

        avg_chen_enter = chen_enter_scores.mean() if len(chen_enter_scores) > 0 else float("nan")
        avg_chen_fold  = chen_fold_scores.mean()  if len(chen_fold_scores)  > 0 else float("nan")

        # ── Win / chips ────────────────────────────────────────────────────
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

        records.append({
            "player_id":        player_id,
            "display_name":     name,
            "total_actions":    total,
            "hands_seen":       hands_seen,
            "vpip_pct":         round(vpip_pct, 1),
            "pfr_pct":          round(pfr_pct, 1),
            "af":               af_display,
            "_af_raw":          af_raw,
            "afq":              round(afq, 1),
            "fold_rate":        round(fold_rate, 1),
            "pre_fold_rate":    round(pre_fold_rate, 1),
            "avg_raise_size":   round(avg_raise, 1),
            "error_rate":              round(error_rate, 1),
            "n_parse_error":           int(n_parse_error),
            "n_parse_error_rescued":   int(n_parse_error_rescued),
            "n_timeout":               int(n_timeout),
            "n_api_error":             int(n_api_error),
            "n_any_error":             int(n_any_error),
            "avg_thinking_len": round(avg_think_len, 0),
            "avg_chen_enter":   round(avg_chen_enter, 2) if pd.notna(avg_chen_enter) else float("nan"),
            "avg_chen_fold":    round(avg_chen_fold,  2) if pd.notna(avg_chen_fold)  else float("nan"),
            "chips_won":        chips_won,
            "hands_won":        hands_won,
            "win_rate_pct":     round(win_rate, 1),
        })

    df = pd.DataFrame(records)
    df = df.set_index("player_id")
    return df


# ── Personality Classification ────────────────────────────────────────────────
#
# Standard poker archetypes:
#   TAG  (Tight-Aggressive)  — VPIP<22%, AF>1.2  — selective + aggressive
#   LAG  (Loose-Aggressive)  — VPIP≥22%, AF>1.2  — wide range + aggressive
#   Nit  (Tight-Passive)     — VPIP<22%, AF≤1.2  — very selective + passive
#   Fish (Loose-Passive)     — VPIP≥22%, AF≤1.2  — calls too much, rarely raises
#
PERSONALITY_COLORS = {
    "TAG":  "#2196F3",   # blue
    "LAG":  "#F44336",   # red
    "Nit":  "#9E9E9E",   # grey
    "Fish": "#FF9800",   # orange
}


def classify_personality(row) -> tuple[str, str]:
    """Returns (label, description) for a player row."""
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


# ── Thinking-Log Analysis ─────────────────────────────────────────────────────

KEYWORD_SETS = {
    "aggressive":   ["bluff", "pressure", "represent", "steal", "aggress", "push",
                     "force", "dominate", "squeeze"],
    "conservative": ["fold", "conserv", "cautious", "protect", "safe", "careful",
                     "preserve", "chip", "risk avers"],
    "analytical":   ["equity", "pot odds", "outs", "implied", "range", "expected value",
                     " ev ", "calculate", "probability", "percent", "equity"],
    "uncertain":    ["not sure", "unclear", "might", "maybe", "possibly", "risky",
                     "gamble", "guess", "hope"],
}


def analyze_thinking(actions: pd.DataFrame) -> dict[str, dict[str, float]]:
    """
    For each player, compute keyword-density scores (per 1000 words) across
    four behavioral dimensions: aggressive, conservative, analytical, uncertain.
    """
    results = {}

    for pid in actions["player_id"].unique():
        pa   = actions[actions["player_id"] == pid]
        text = " ".join(pa["thinking"].dropna().str.lower().tolist())

        if not text.strip():
            results[pid] = {k: 0.0 for k in KEYWORD_SETS}
            continue

        n_words = max(len(text.split()), 1)

        def density(keywords):
            return round(sum(text.count(kw) for kw in keywords) / n_words * 1000, 2)

        results[pid] = {cat: density(kws) for cat, kws in KEYWORD_SETS.items()}

    return results


# ── Visualizations ────────────────────────────────────────────────────────────

def _player_label(metrics: pd.DataFrame, pid: str) -> str:
    return metrics.loc[pid, "display_name"] if pid in metrics.index else pid


def plot_action_distribution(actions: pd.DataFrame, metrics: pd.DataFrame, out: Path):
    """Stacked horizontal % bar chart showing action breakdown per player."""
    if not MATPLOTLIB_AVAILABLE:
        return

    # The engine only emits fold/check/call/raise — "bet" never appears.
    action_order = ["fold", "check", "call", "raise"]
    bar_colors   = {
        "fold":  "#EF5350",
        "check": "#BDBDBD",
        "call":  "#42A5F5",
        "raise": "#FF7043",
        "other": "#78909C",
    }

    rows, labels = [], []
    for pid in metrics.index:
        pa = actions[actions["player_id"] == pid]
        total = len(pa)
        if total == 0:
            continue
        counts = {a: (pa["action_type"] == a).sum() for a in action_order}
        counts["other"] = total - sum(counts.values())
        rows.append({k: counts[k] / total * 100 for k in list(action_order) + ["other"]})
        labels.append(metrics.loc[pid, "display_name"])

    df = pd.DataFrame(rows, index=labels)

    fig, ax = plt.subplots(figsize=(13, max(4, len(df) * 0.65)))
    left = np.zeros(len(df))

    for col in list(action_order) + ["other"]:
        if col not in df.columns:
            continue
        vals = df[col].values
        ax.barh(df.index, vals, left=left, color=bar_colors[col], label=col, height=0.55)
        for i, (v, l) in enumerate(zip(vals, left)):
            if v > 4:
                ax.text(l + v / 2, i, f"{v:.0f}%", ha="center", va="center",
                        fontsize=7.5, color="white", fontweight="bold")
        left += vals

    ax.set_xlabel("% of Total Actions", fontsize=11)
    ax.set_title("Action Distribution by Player", fontsize=14, fontweight="bold")
    ax.set_xlim(0, 100)
    ax.legend(loc="lower right", ncol=3, fontsize=9)
    ax.invert_yaxis()
    plt.tight_layout()
    plt.savefig(out / "action_distribution.png", dpi=150)
    plt.close()
    print("  ✓ action_distribution.png")


def plot_aggression_scatter(metrics: pd.DataFrame, out: Path):
    """
    VPIP vs Aggression Factor scatter — classic player-type quadrant chart.
    Vertical line at VPIP=22%, horizontal at AF=1.2 delineate the four archetypes.
    """
    if not MATPLOTLIB_AVAILABLE:
        return

    fig, ax = plt.subplots(figsize=(9, 7))

    # Shaded quadrant backgrounds
    ax.axvspan(0,  22, 0, 1, alpha=0.04, color="#9E9E9E")
    ax.axvspan(22, 100, 0, 1, alpha=0.04, color="#FF9800")
    ax.axhline(1.2, color="#BDBDBD", linewidth=1, linestyle="--")
    ax.axvline(22,  color="#BDBDBD", linewidth=1, linestyle="--")

    ax.text(11, 0.15, "Nit/Rock", ha="center", color="#9E9E9E", fontsize=9, style="italic")
    ax.text(60, 0.15, "Fish / Calling Station", ha="center", color="#FF9800", fontsize=9, style="italic")
    ax.text(11, max(metrics["_af_raw"].max() * 0.85, 1.5),
            "TAG", ha="center", color="#2196F3", fontsize=10, fontweight="bold")
    ax.text(60, max(metrics["_af_raw"].max() * 0.85, 1.5),
            "LAG", ha="center", color="#F44336", fontsize=10, fontweight="bold")

    for pid, row in metrics.iterrows():
        personality, _ = classify_personality(row)
        color = PERSONALITY_COLORS.get(personality, "#607D8B")
        x, y = row["vpip_pct"], row["_af_raw"]
        ax.scatter(x, y, color=color, s=220, zorder=5, edgecolors="white", linewidth=2)
        ax.annotate(
            row["display_name"], (x, y),
            textcoords="offset points", xytext=(10, 4),
            fontsize=8.5, color="#333",
        )

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
    ax.set_ylim(0, max(5.5, metrics["_af_raw"].max() + 0.6))

    plt.tight_layout()
    plt.savefig(out / "aggression_profile.png", dpi=150)
    plt.close()
    print("  ✓ aggression_profile.png")


def plot_performance_ranking(metrics: pd.DataFrame, out: Path):
    """Horizontal bar chart of chips won (gross, from pot winnings)."""
    if not MATPLOTLIB_AVAILABLE:
        return

    df = metrics.sort_values("chips_won", ascending=True)
    colors = ["#2196F3" if x >= 0 else "#EF5350" for x in df["chips_won"]]

    fig, ax = plt.subplots(figsize=(10, max(4, len(df) * 0.7)))
    bars = ax.barh(df["display_name"], df["chips_won"], color=colors,
                   edgecolor="white", height=0.55)

    for bar, val in zip(bars, df["chips_won"]):
        w = bar.get_width()
        ax.text(
            w + 8 if w >= 0 else w - 8,
            bar.get_y() + bar.get_height() / 2,
            f"{val:,.0f}",
            va="center", ha="left" if w >= 0 else "right",
            fontsize=9, fontweight="bold",
        )

    ax.axvline(0, color="#555", linewidth=1.2)
    ax.set_xlabel("Total Chips Won from Pots", fontsize=11)
    ax.set_title("Performance Ranking — Chips Won", fontsize=14, fontweight="bold")
    low = min(df["chips_won"].min() * 1.25, -30)
    high = max(df["chips_won"].max() * 1.25, 30)
    ax.set_xlim(low, high)
    ax.invert_yaxis()
    plt.tight_layout()
    plt.savefig(out / "performance_ranking.png", dpi=150)
    plt.close()
    print("  ✓ performance_ranking.png")


def plot_chen_vs_action(actions: pd.DataFrame, metrics: pd.DataFrame, out: Path):
    """
    Box plots: preflop Chen score stratified by action type (fold / call / raise).
    One subplot per player. Helps reveal whether a player enters with strong hands.
    """
    if not MATPLOTLIB_AVAILABLE:
        return

    pre = actions[actions["phase"] == "preflop"].copy()
    pre["chen_score"] = pd.to_numeric(pre.get("chen_score", None), errors="coerce")
    pre = pre.dropna(subset=["chen_score"])

    if pre.empty:
        print("  ⚠ Skipping chen_vs_action (no preflop Chen data)")
        return

    pids = [p for p in metrics.index if p in pre["player_id"].values]
    n    = len(pids)
    if n == 0:
        return

    fig, axes = plt.subplots(1, n, figsize=(max(14, n * 3), 5), sharey=True)
    if n == 1:
        axes = [axes]

    act_colors = {"fold": "#EF5350", "call": "#42A5F5", "raise": "#FF7043"}
    act_order  = ["fold", "call", "raise"]

    for ax, pid in zip(axes, pids):
        pdata  = pre[pre["player_id"] == pid]
        groups = {}
        for act in act_order:
            scores = pdata[pdata["action_type"] == act]["chen_score"].dropna()
            if len(scores) > 0:
                groups[act] = scores.tolist()

        if groups:
            positions = list(range(1, len(groups) + 1))
            bp = ax.boxplot(
                [groups[k] for k in groups],
                patch_artist=True, positions=positions, widths=0.5,
                medianprops=dict(color="white", linewidth=2),
            )
            for patch, key in zip(bp["boxes"], groups.keys()):
                patch.set_facecolor(act_colors.get(key, "#aaa"))
                patch.set_alpha(0.8)
            ax.set_xticks(positions)
            ax.set_xticklabels(list(groups.keys()), fontsize=8)

        name = metrics.loc[pid, "display_name"]
        personality, _ = classify_personality(metrics.loc[pid])
        color = PERSONALITY_COLORS.get(personality, "#607D8B")
        ax.set_title(f"{name}\n[{personality}]", fontsize=8.5, fontweight="bold", color=color)
        ax.set_ylim(-1, 22)
        ax.axhline(8, color="#BDBDBD", linewidth=0.8, linestyle="--")  # "premium" threshold

    axes[0].set_ylabel("Chen Hand Strength Score", fontsize=10)
    axes[0].text(0.01, 8.3, "▲ premium (≥8)", fontsize=7, color="#888",
                 transform=axes[0].get_yaxis_transform())

    fig.suptitle("Preflop Hand Strength (Chen Score) by Action", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig(out / "chen_vs_action.png", dpi=150)
    plt.close()
    print("  ✓ chen_vs_action.png")


def plot_radar_chart(metrics: pd.DataFrame, out: Path):
    """
    Spider/radar chart comparing players across 5 normalized dimensions:
    VPIP, PFR, AFq, Fold Rate, Thinking Depth.
    """
    if not MATPLOTLIB_AVAILABLE:
        return

    dims = ["VPIP %", "PFR %", "AFq %", "Fold Rate %", "Thinking Depth"]

    # Build normalized series per dimension
    raw = {
        "VPIP %":         metrics["vpip_pct"],
        "PFR %":          metrics["pfr_pct"],
        "AFq %":          metrics["afq"],
        "Fold Rate %":    metrics["fold_rate"],
        "Thinking Depth": metrics["avg_thinking_len"],
    }

    normed = {}
    for dim, col in raw.items():
        mn, mx = col.min(), col.max()
        if mx > mn:
            normed[dim] = (col - mn) / (mx - mn)
        else:
            normed[dim] = col * 0.0  # all same → zero

    N      = len(dims)
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(9, 9), subplot_kw=dict(polar=True))
    colors  = plt.cm.tab10.colors

    for i, (pid, row) in enumerate(metrics.iterrows()):
        name   = row["display_name"]
        values = [normed[dim][pid] for dim in dims]
        values += values[:1]
        color  = colors[i % len(colors)]
        ax.plot(angles, values, "-o", color=color, linewidth=2, label=name)
        ax.fill(angles, values, color=color, alpha=0.07)

    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_thetagrids(np.degrees(angles[:-1]), dims, fontsize=10)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["25%", "50%", "75%", "100%"], fontsize=7)
    ax.set_title("Player Personality Radar\n(normalized per dimension)", fontsize=13,
                 fontweight="bold", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.38, 1.12), fontsize=9)

    plt.tight_layout()
    plt.savefig(out / "personality_radar.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ personality_radar.png")


def plot_thinking_keywords(kw_analysis: dict, metrics: pd.DataFrame, out: Path):
    """Grouped bar chart of thinking-log keyword densities per behavioral category."""
    if not MATPLOTLIB_AVAILABLE:
        return

    categories = list(KEYWORD_SETS.keys())
    colors     = ["#F44336", "#4CAF50", "#2196F3", "#FF9800"]
    pids       = [p for p in metrics.index if p in kw_analysis]
    names      = [metrics.loc[p, "display_name"] for p in pids]

    if not pids:
        return

    x     = np.arange(len(pids))
    width = 0.18

    fig, ax = plt.subplots(figsize=(13, 5))
    for i, (cat, color) in enumerate(zip(categories, colors)):
        vals = [kw_analysis[pid][cat] for pid in pids]
        ax.bar(x + i * width, vals, width, label=cat.capitalize(),
               color=color, alpha=0.85, edgecolor="white")

    ax.set_xticks(x + width * 1.5)
    ax.set_xticklabels(names, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Keyword Density (per 1 000 words)", fontsize=10)
    ax.set_title("Thinking Log Keyword Analysis", fontsize=14, fontweight="bold")
    ax.legend(fontsize=9)
    plt.tight_layout()
    plt.savefig(out / "thinking_keywords.png", dpi=150)
    plt.close()
    print("  ✓ thinking_keywords.png")


def plot_error_breakdown(metrics: pd.DataFrame, out: Path):
    """
    Stacked horizontal bar chart showing error type breakdown per player.
    Only renders if at least one error exists in the dataset.
    """
    if not MATPLOTLIB_AVAILABLE:
        return

    error_cols   = ["n_parse_error", "n_parse_error_rescued", "n_timeout", "n_api_error"]
    error_labels = ["Parse Error (fallback)", "Parse Rescued (guardrail)", "Timeout", "API Error"]
    error_colors = ["#EF5350", "#FF9800", "#9C27B0", "#607D8B"]

    # Only include players who had at least one error
    has_errors = metrics[error_cols].sum(axis=1) > 0
    df = metrics[has_errors].copy()

    if df.empty:
        print("  ⚠ Skipping error_breakdown (no errors recorded)")
        return

    # Build absolute counts and total-actions denominators
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
                        fontsize=8, color="white", fontweight="bold")
        left += vals

    # Annotate total error rate on the right
    for i, (total_err, total_act) in enumerate(zip(left, totals)):
        rate = total_err / total_act * 100 if total_act > 0 else 0
        ax.text(left[i] + 0.2, i, f"  {rate:.0f}% of {int(total_act)} moves",
                va="center", fontsize=8, color="#ccc")

    ax.set_xlabel("Number of Errors", fontsize=11)
    ax.set_title("LLM Output Error Breakdown by Player", fontsize=14, fontweight="bold")
    ax.legend(loc="lower right", fontsize=9)
    ax.invert_yaxis()
    plt.tight_layout()
    plt.savefig(out / "error_breakdown.png", dpi=150)
    plt.close()
    print("  ✓ error_breakdown.png")


# ── Report Generation ─────────────────────────────────────────────────────────

def generate_report(
    metrics: pd.DataFrame,
    kw_analysis: dict,
    actions: pd.DataFrame,
    hands: pd.DataFrame,
) -> str:
    now        = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_games    = actions["game_id"].nunique()
    n_hands    = len(actions[["game_id", "hand_number"]].drop_duplicates())
    n_actions  = len(actions)
    n_players  = len(metrics)

    ranked = metrics.sort_values("chips_won", ascending=False)

    lines = [
        "# Texas Hold'em LLM Arena — Analysis Report",
        f"*Generated: {now}*",
        "",
        "---",
        "",
        "## Overview",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Games analyzed | {n_games} |",
        f"| Hands played | {n_hands} |",
        f"| Players | {n_players} |",
        f"| Total actions logged | {n_actions} |",
        "",
        "---",
        "",
        "## Performance Ranking",
        "",
        "Ranked by total chips won from pots (gross). "
        "Note: chips won reflects pot winnings; net profit also depends on "
        "chips invested per hand.",
        "",
        "| Rank | Player | Chips Won | Hands Won | Win Rate | Archetype |",
        "|------|--------|-----------|-----------|----------|-----------|",
    ]

    for rank, (pid, row) in enumerate(ranked.iterrows(), 1):
        personality, _ = classify_personality(row)
        lines.append(
            f"| {rank} | **{row['display_name']}** | {row['chips_won']:,.0f} | "
            f"{row['hands_won']} | {row['win_rate_pct']}% | {personality} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Strategy Metrics",
        "",
        "| Player | VPIP% | PFR% | AF | AFq% | Fold% | Pre-Fold% | Avg Raise | Error% | Think Len |",
        "|--------|-------|------|----|------|-------|-----------|-----------|--------|-----------|",
    ]

    for pid, row in metrics.iterrows():
        lines.append(
            f"| {row['display_name']} | {row['vpip_pct']} | {row['pfr_pct']} | "
            f"{row['af']} | {row['afq']} | {row['fold_rate']} | {row['pre_fold_rate']} | "
            f"{row['avg_raise_size']} | {row['error_rate']} | {row['avg_thinking_len']:.0f} |"
        )

    lines += [
        "",
        "**Metric glossary:**",
        "",
        "| Metric | Definition |",
        "|--------|------------|",
        "| **VPIP%** | Voluntarily Put $ In Pot — % of hands with a preflop call or raise |",
        "| **PFR%**  | Preflop Raise % |",
        "| **AF**    | Aggression Factor = (bets+raises) ÷ calls, postflop only; >1.5 = aggressive |",
        "| **AFq%**  | Aggression Frequency — % of non-fold actions that are bets/raises |",
        "| **Fold%** | Overall fold rate across all streets |",
        "| **Pre-Fold%** | Preflop-only fold rate |",
        "| **Avg Raise** | Mean bet/raise size in chips (postflop only) |",
        "| **Error%** | Total LLM output error rate (all error types combined) |",
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
        total   = int(row["total_actions"])
        pe      = int(row["n_parse_error"])
        pr      = int(row["n_parse_error_rescued"])
        to      = int(row["n_timeout"])
        ae      = int(row["n_api_error"])
        er      = row["error_rate"]
        # Highlight bad rates
        rate_str = f"**{er:.1f}%**" if er >= 20 else f"{er:.1f}%"
        lines.append(
            f"| {row['display_name']} | {total} | {pe} | {pr} | {to} | {ae} | {rate_str} |"
        )

    lines += [
        "",
        "**Error type glossary:**",
        "",
        "| Type | Meaning |",
        "|------|---------|",
        "| **Parse Error** | Model output could not be parsed; a fallback action (fold/check) was used |",
        "| **Rescued** | Primary parse failed but a secondary guardrail extracted a valid action |",
        "| **Timeout** | Model API call exceeded the time limit; fallback action used |",
        "| **API Error** | Network or upstream API failure |",
        "",
        "---",
        "",
        "## Personality Profiles",
        "",
        "Players are classified into four canonical poker archetypes using VPIP% and "
        "Aggression Factor (AF):",
        "",
        "| Archetype | VPIP | AF | Play Style |",
        "|-----------|------|----|------------|",
        "| **TAG** (Tight-Aggressive) | <22% | >1.2 | The 'pro' style: patient entry, strong betting |",
        "| **LAG** (Loose-Aggressive) | ≥22% | >1.2 | Wide range + aggression; high-variance |",
        "| **Nit/Rock**               | <22% | ≤1.2 | Overly selective; passive when in hand |",
        "| **Fish** (Calling Station) | ≥22% | ≤1.2 | Calls too much, rarely raises |",
        "",
    ]

    for pid, row in metrics.iterrows():
        personality, description = classify_personality(row)
        kw = kw_analysis.get(pid, {k: 0.0 for k in KEYWORD_SETS})

        dominant_style = max(kw, key=kw.get) if any(kw.values()) else "N/A"
        chen_enter = row["avg_chen_enter"]
        chen_fold  = row["avg_chen_fold"]
        chen_enter_str = f"{chen_enter:.1f}" if pd.notna(chen_enter) else "N/A"
        chen_fold_str  = f"{chen_fold:.1f}"  if pd.notna(chen_fold)  else "N/A"

        color_tag = PERSONALITY_COLORS.get(personality, "#607D8B")

        lines += [
            f"### {row['display_name']}  `[{personality}]`",
            "",
            f"> _{description}_",
            "",
            "**Strategic summary:**",
            "",
            f"- Voluntarily entered **{row['vpip_pct']}%** of hands (VPIP); "
            f"raised preflop in **{row['pfr_pct']}%** (PFR)",
            f"- Post-flop Aggression Factor: **{row['af']}** "
            f"(>1.5 = aggressive, <1.0 = passive/calling station)",
            f"- Aggression Frequency: **{row['afq']}%** of non-fold actions were bets/raises",
            f"- Overall fold rate: **{row['fold_rate']}%** "
            f"| Preflop fold rate: **{row['pre_fold_rate']}%**",
            f"- Average bet/raise size (postflop): **{row['avg_raise_size']} chips**",
            f"- Chen hand score when *entering* pot: **{chen_enter_str}** "
            f"| when *folding*: **{chen_fold_str}** "
            f"(scale 1–20; ≥8 = premium hand)",
            f"- LLM output error rate: **{row['error_rate']}%** "
            f"({int(row['n_parse_error'])} parse errors, "
            f"{int(row['n_parse_error_rescued'])} rescued, "
            f"{int(row['n_timeout'])} timeouts, "
            f"{int(row['n_api_error'])} API errors"
            f" out of {int(row['total_actions'])} moves)",
            f"- Average reasoning length: **{row['avg_thinking_len']:.0f} chars**",
            "",
            f"**Thinking style** (keyword density, dominant: _{dominant_style}_):",
            "",
            "| Aggressive | Conservative | Analytical | Uncertain |",
            "|------------|--------------|------------|-----------|",
            f"| {kw.get('aggressive', 0):.2f} | {kw.get('conservative', 0):.2f} "
            f"| {kw.get('analytical', 0):.2f} | {kw.get('uncertain', 0):.2f} |",
            "",
            "*(values = keyword occurrences per 1 000 words of reasoning text)*",
            "",
        ]

    lines += [
        "---",
        "",
        "## Visualizations",
        "",
        "All charts are saved to `analysis/output/`:",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `action_distribution.png` | Stacked bar: fold / check / call / raise breakdown |",
        "| `aggression_profile.png` | VPIP vs AF scatter with personality quadrants |",
        "| `performance_ranking.png` | Chips-won ranking bar chart |",
        "| `chen_vs_action.png` | Preflop hand strength (Chen score) by action type |",
        "| `personality_radar.png` | Multi-dimensional radar chart per player |",
        "| `thinking_keywords.png` | Thinking-log keyword density by behavioral category |",
        "| `error_breakdown.png` | LLM output error counts by type per player |",
        "",
        "---",
        "",
        "*Analysis powered by Texas Hold'em LLM Arena — "
        "metrics based on standard poker strategy theory (VPIP, PFR, AF/AFq, Chen formula).*",
    ]

    return "\n".join(lines)


# ── Data loading (single folder or all folders) ───────────────────────────────

def load_game_dir(game_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load actions.csv and hands.csv from a single game folder."""
    action_frames, hand_frames = [], []
    af, hf = game_dir / "actions.csv", game_dir / "hands.csv"
    if af.exists():
        df = pd.read_csv(af, dtype=str); df["_folder"] = game_dir.name; action_frames.append(df)
    if hf.exists():
        df = pd.read_csv(hf, dtype=str); df["_folder"] = game_dir.name; hand_frames.append(df)
    if not action_frames:
        print(f"No actions.csv found in {game_dir}"); sys.exit(1)
    actions = pd.concat(action_frames, ignore_index=True)
    hands   = pd.concat(hand_frames,   ignore_index=True) if hand_frames else pd.DataFrame()
    return _coerce_and_score(actions, hands)


def _coerce_and_score(actions: pd.DataFrame, hands: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Numeric coercions + Chen score pre-computation shared by both load paths."""
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


# ── Main ──────────────────────────────────────────────────────────────────────

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
            "Output is written into that same folder. "
            "Omit to analyze ALL game folders under data/ and write to analysis/output/."
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
            print(f"Error: {game_dir} is not a directory."); sys.exit(1)
        out_dir = game_dir
        print(f"► Loading data from {game_dir} ...")
        actions, hands = load_game_dir(game_dir)
    else:
        out_dir = OUT_DIR
        print("► Loading all game data...")
        actions, hands = load_all_data()

    out_dir.mkdir(exist_ok=True)

    print("► Computing player metrics...")
    metrics = compute_player_metrics(actions, hands)
    metrics["personality"] = metrics.apply(
        lambda r: classify_personality(r)[0], axis=1
    )

    print("► Analyzing thinking logs...")
    kw_analysis = analyze_thinking(actions)

    print()
    if MATPLOTLIB_AVAILABLE:
        print("► Generating charts...")
        plot_action_distribution(actions, metrics, out_dir)
        plot_aggression_scatter(metrics, out_dir)
        plot_performance_ranking(metrics, out_dir)
        plot_chen_vs_action(actions, metrics, out_dir)
        plot_radar_chart(metrics, out_dir)
        plot_thinking_keywords(kw_analysis, metrics, out_dir)
        plot_error_breakdown(metrics, out_dir)
    else:
        print("► Skipping charts (matplotlib not installed)")

    print()
    print("► Writing report and metrics CSV...")
    report_text = generate_report(metrics, kw_analysis, actions, hands)
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

    # ── Console summary ──────────────────────────────────────────────────────
    print("PERFORMANCE RANKING")
    print(f"  {'Rank':<5} {'Player':<22} {'Chips Won':>10} {'W/L':>6} {'Style':<6}")
    print(f"  {'-'*5} {'-'*22} {'-'*10} {'-'*6} {'-'*6}")
    ranked = metrics.sort_values("chips_won", ascending=False)
    for i, (pid, row) in enumerate(ranked.iterrows(), 1):
        wl = f"{row['hands_won']}/{int(row['hands_seen'])}"
        print(
            f"  {i:<5} {row['display_name']:<22} {row['chips_won']:>10,.0f} "
            f"{wl:>6}  [{row['personality']}]"
        )
    print()


if __name__ == "__main__":
    main()
