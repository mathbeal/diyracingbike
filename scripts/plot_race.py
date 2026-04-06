#!/usr/bin/env python3
"""
plot_race.py — Visualise race results from a JSON output file.

Requires state_log in the JSON (produced by race.py with --output).

Usage:
    python scripts/plot_race.py results.json
    python scripts/plot_race.py results.json --output race_plot.png
"""

import argparse
import json
import os
import sys

# Set non-interactive backend before pyplot is imported.
# Use Agg when saving to a file or when no display is available.
if "--output" in sys.argv or "-o" in sys.argv or not os.environ.get("DISPLAY"):
    os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# Team colors matching the ASCII renderer
TEAM_COLORS = {"A": "#4a90d9", "B": "#e6b800", "C": "#5cb85c"}
LINE_STYLES = ["solid", "dashed", "dotted"]


def plot(data: dict, output_path: str | None) -> None:
    track_length = data["config_summary"]["track_length"]
    state_log = data.get("state_log")

    if not state_log:
        print(
            "Error: state_log not found in results file.\n"
            "Re-run the race with --output to generate it.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build per-cyclist time series
    # cyclists_data[id] = {"team": str, "pos": [int...], "energy": [int...], "ticks": [int...]}
    cyclists_data: dict[str, dict] = {}
    for snap in state_log:
        tick = snap["tick"]
        for c in snap["cyclists"]:
            cid = c["id"]
            if cid not in cyclists_data:
                cyclists_data[cid] = {"team": c["team"], "pos": [], "energy": [], "ticks": []}
            cyclists_data[cid]["ticks"].append(tick)
            cyclists_data[cid]["pos"].append(min(c["pos"], track_length))
            cyclists_data[cid]["energy"].append(c["energy"])

    # Sort cyclists by team then id for consistent legend ordering
    sorted_ids = sorted(cyclists_data, key=lambda cid: (cyclists_data[cid]["team"], cid))
    teams = sorted(set(d["team"] for d in cyclists_data.values()))

    fig, (ax_pos, ax_en) = plt.subplots(
        2, 1, figsize=(12, 7), sharex=True,
        gridspec_kw={"height_ratios": [3, 2]},
    )
    fig.suptitle(
        f"Race Results — Winner: Team {data['winner']}  |  {data['ticks']} ticks  |  "
        f"Finish order: {', '.join(data['finished_order'][:5])}{'…' if len(data['finished_order']) > 5 else ''}",
        fontsize=11, fontweight="bold",
    )

    # --- Position plot ---
    team_style_counter: dict[str, int] = {t: 0 for t in teams}
    for cid in sorted_ids:
        d = cyclists_data[cid]
        team = d["team"]
        style_idx = team_style_counter[team]
        team_style_counter[team] += 1
        ax_pos.plot(
            d["ticks"], d["pos"],
            color=TEAM_COLORS[team],
            linestyle=LINE_STYLES[style_idx % len(LINE_STYLES)],
            linewidth=1.8,
            label=cid,
            alpha=0.9,
        )

    # Finish line
    ax_pos.axhline(track_length, color="red", linestyle="--", linewidth=1.2, alpha=0.6, label="Finish")

    ax_pos.set_ylabel("Position (cells)")
    ax_pos.set_ylim(0, track_length * 1.05)
    ax_pos.yaxis.set_major_locator(ticker.MultipleLocator(max(1, track_length // 6)))
    ax_pos.grid(axis="y", alpha=0.3)
    ax_pos.grid(axis="x", alpha=0.15)

    # Legend: group by team, add finish line at end
    _add_legend(ax_pos, sorted_ids, cyclists_data, teams, include_finish=True)

    # --- Energy plot ---
    team_style_counter = {t: 0 for t in teams}
    for cid in sorted_ids:
        d = cyclists_data[cid]
        team = d["team"]
        style_idx = team_style_counter[team]
        team_style_counter[team] += 1
        ax_en.plot(
            d["ticks"], d["energy"],
            color=TEAM_COLORS[team],
            linestyle=LINE_STYLES[style_idx % len(LINE_STYLES)],
            linewidth=1.8,
            alpha=0.9,
        )

    ax_en.set_xlabel("Tick")
    ax_en.set_ylabel("Energy (1–5)")
    ax_en.set_ylim(0.5, 5.5)
    ax_en.yaxis.set_major_locator(ticker.MultipleLocator(1))
    ax_en.grid(axis="y", alpha=0.3)
    ax_en.grid(axis="x", alpha=0.15)

    # Shade energy danger zone (exhausted ≤ 2)
    ax_en.axhspan(0.5, 2.5, color="red", alpha=0.06, label="Exhausted zone (≤2)")
    ax_en.legend(fontsize=8, loc="upper right")

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {output_path}")
    else:
        plt.show()


def _add_legend(ax, sorted_ids, cyclists_data, teams, include_finish=False):
    """Groups legend entries by team with a team header."""
    handles, labels = [], []
    for team in teams:
        # Add a blank spacer label acting as team header
        handles.append(plt.Line2D([], [], color=TEAM_COLORS[team], linewidth=4, alpha=0.5))
        labels.append(f"Team {team}")
        team_counter = 0
        for cid in sorted_ids:
            if cyclists_data[cid]["team"] != team:
                continue
            style = LINE_STYLES[team_counter % len(LINE_STYLES)]
            handles.append(
                plt.Line2D([], [], color=TEAM_COLORS[team], linestyle=style, linewidth=1.5)
            )
            labels.append(f"  {cid}")
            team_counter += 1
    if include_finish:
        handles.append(plt.Line2D([], [], color="red", linestyle="--", linewidth=1.2, alpha=0.6))
        labels.append("Finish line")
    ax.legend(handles, labels, fontsize=8, loc="upper left", ncol=len(teams) + 1)


def main():
    parser = argparse.ArgumentParser(description="Plot race results from JSON output")
    parser.add_argument("results", help="Path to race results JSON (produced with --output)")
    parser.add_argument("--output", "-o", default=None, help="Save plot to this file (PNG/SVG/PDF)")
    args = parser.parse_args()

    with open(args.results) as f:
        data = json.load(f)

    plot(data, args.output)


if __name__ == "__main__":
    main()
