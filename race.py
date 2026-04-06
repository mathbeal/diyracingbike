# -*- coding: utf-8 -*-
"""
Cycling Race — Async Agents with Claude
Educational goal: understand asyncio.gather + orchestrator/sub-agent pattern
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Literal
import argparse
import asyncio
import json
import os
import random
from dotenv import load_dotenv
import anthropic

load_dotenv()

# =============================================================================
# MODEL
# =============================================================================

Action = Literal["advance", "slow", "draft", "potion", "wait"]


@dataclass
class Cyclist:
    id: str           # "A1", "B2", "C3"…
    team: str         # "A", "B", "C"
    pos: int          # 1D position (0=start, track_length=finish)
    energy: int       # 1..5 (1=exhausted, 5=full)
    potion_used: bool # True if potion already consumed


@dataclass
class RaceState:
    track_length: int        # number of cells (e.g. 60)
    cyclists: list[Cyclist]  # sorted by descending position
    tick: int
    finished: list[str]      # ids in finishing order


# =============================================================================
# AGENTS
# =============================================================================

_VALID_ACTIONS: set[str] = {"advance", "slow", "draft", "potion", "wait"}


def build_prompt(cyclist: Cyclist, state: RaceState) -> str:
    """Builds the prompt sent to Claude for this cyclist."""
    others = [c for c in state.cyclists if c.id != cyclist.id]

    # Cyclists ahead (within 5 cells)
    ahead = [c for c in others if 0 < c.pos - cyclist.pos <= 5]
    ahead_str = ", ".join(f"{c.id}(team {c.team}) at {c.pos - cyclist.pos} cell(s)" for c in ahead)

    # Cyclists behind (within 5 cells)
    behind = [c for c in others if 0 < cyclist.pos - c.pos <= 5]
    behind_str = ", ".join(f"{c.id}(team {c.team}) at {cyclist.pos - c.pos} cell(s)" for c in behind)

    # Teammates
    teammates = [c for c in state.cyclists if c.team == cyclist.team and c.id != cyclist.id]
    team_str = "  ".join(f"{c.id}:#{c.pos}" for c in teammates)

    potion_status = "already used" if cyclist.potion_used else "available"

    return f"""You are cyclist {cyclist.id} (team {cyclist.team}).
Tick {state.tick} | Position #{cyclist.pos}/{state.track_length} | Energy: {cyclist.energy}/5

Ahead of you (≤5 cells): {ahead_str or "nobody"}
Behind you (≤5 cells): {behind_str or "nobody"}
Teammates: {team_str}
Potion: {potion_status}

Speed mechanic: your speed = your energy (e.g. energy 5 → advance 5 cells).
Energy cost when advancing: -(energy÷2 rounded down), e.g. energy 5 → -2.
Drafting (≤5 cells behind a leader): +1 energy bonus, even when advancing.

Strategy: save energy by drafting when possible.
If you are in the lead and exhausted (energy 1-2), slow down to recover.
Use the potion at the right moment (final sprint or to catch up).

Reply with ONLY one word from: advance | slow | draft | potion | wait"""


def parse_action(text: str) -> Action:
    """
    Extracts a valid action from the text returned by Claude.
    Falls back to "advance" if no valid word is found.
    """
    text = text.strip().lower()
    # Look for a valid word in the text (Claude may reply "I choose draft")
    for word in text.split():
        clean = word.strip(".,!?:;\"'")
        if clean in _VALID_ACTIONS:
            return clean  # type: ignore
    return "advance"  # fallback


# Anthropic client (singleton)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))


async def cyclist_agent(cyclist: Cyclist, state: RaceState) -> tuple[str, Action]:
    """
    Sub-agent: calls Claude to decide the cyclist's action.

    EDUCATIONAL NOTE:
    - The Anthropic Python SDK is SYNCHRONOUS (client.messages.create blocks the thread)
    - asyncio.to_thread() runs it in a thread pool → does not block the async loop
    - Each cyclist_agent is an independent coroutine
    """
    prompt = build_prompt(cyclist, state)

    response = await asyncio.to_thread(
        client.messages.create,
        model="claude-haiku-4-5-20251001",
        max_tokens=10,  # We only want one word → minimal latency
        messages=[{"role": "user", "content": prompt}],
    )

    action = parse_action(response.content[0].text)
    return (cyclist.id, action)


async def orchestrator(state: RaceState) -> dict[str, Action]:
    """
    Orchestrator: launches all agents in parallel with asyncio.gather().

    EDUCATIONAL NOTE:
    - asyncio.gather(*tasks) starts all coroutines SIMULTANEOUSLY
    - We wait until the SLOWEST one has replied (not the fastest)
    - return_exceptions=True: a failing agent does not block the others
    - Total time ≈ max(individual latencies), not their sum
    """
    active = [c for c in state.cyclists if c.id not in state.finished]

    # Create the coroutines (not yet started)
    tasks = [cyclist_agent(c, state) for c in active]

    # Launch EVERYTHING in parallel — this is where the async magic happens
    results = await asyncio.gather(*tasks, return_exceptions=True)

    actions: dict[str, Action] = {}
    for i, result in enumerate(results):
        cyclist = active[i]
        if isinstance(result, Exception):
            # Fallback if Claude fails for this cyclist
            print(f"  ⚠ Agent {cyclist.id} failed ({result}), fallback: advance")
            actions[cyclist.id] = "advance"
        else:
            cyclist_id, action = result
            actions[cyclist_id] = action

    return actions


# =============================================================================
# ENGINE
# =============================================================================

def resolve(state: RaceState, actions: dict[str, Action]) -> RaceState:
    """
    Synchronous, deterministic engine.
    1. Computes desired positions (speed = energy for advance/potion)
    2. Resolves collisions (the most advanced cyclist has priority)
    3. Updates energy: cost proportional to speed, recovery when drafting
    4. Detects finishes
    Returns a NEW state — never mutates the existing state.
    """
    # 1. Desired positions — speed = energy when advancing
    desired: dict[str, int] = {}
    for c in state.cyclists:
        if c.id in state.finished:
            desired[c.id] = c.pos
            continue
        action = actions.get(c.id, "advance")
        step = c.energy if action in ("advance", "potion", "draft") else 0
        if step > 0:
            step = max(1, step + random.randint(-1, 1))
        desired[c.id] = c.pos + step

    # 2. Final positions — multiple cyclists can share a cell (overtaking allowed)
    sorted_cyclists = sorted(state.cyclists, key=lambda c: c.pos, reverse=True)
    final_pos: dict[str, int] = {}

    for c in sorted_cyclists:
        final_pos[c.id] = max(desired[c.id], 0)

    # 3. Energy update
    new_cyclists: list[Cyclist] = []
    for c in state.cyclists:
        if c.id in state.finished:
            new_cyclists.append(c)
            continue

        pos = final_pos[c.id]
        action = actions.get(c.id, "advance")
        advancing = action in ("advance", "potion")

        # Anyone within ≤5 cells ahead? (wider window because speeds are higher)
        anyone_ahead = any(
            final_pos[o.id] > pos and final_pos[o.id] <= pos + 5
            for o in state.cyclists if o.id != c.id
        )

        if advancing:
            # Cost proportional to speed (= current energy)
            # Drafting: +1 bonus reduces cost (exhausted cyclist can recover)
            energy_delta = -(c.energy // 2) + (1 if anyone_ahead else 0)
        else:
            # Recovery: +1 when drafting, -1 when leading
            energy_delta = 1 if anyone_ahead else -1

        # Potion
        if action == "potion" and not c.potion_used:
            energy_delta += 3

        new_energy = max(1, min(5, c.energy + energy_delta))
        new_potion = c.potion_used or (action == "potion" and not c.potion_used)

        new_cyclists.append(Cyclist(
            id=c.id,
            team=c.team,
            pos=pos,
            energy=new_energy,
            potion_used=new_potion,
        ))

    # 4. Finishes
    new_finished = list(state.finished)
    # Add in descending position order
    for c in sorted(new_cyclists, key=lambda c: c.pos, reverse=True):
        if c.pos >= state.track_length and c.id not in new_finished:
            new_finished.append(c.id)

    new_cyclists.sort(key=lambda c: c.pos, reverse=True)

    return RaceState(
        track_length=state.track_length,
        cyclists=new_cyclists,
        tick=state.tick + 1,
        finished=new_finished,
    )


# =============================================================================
# RENDERING
# =============================================================================

TEAM_COLORS = {"A": "\033[94m", "B": "\033[93m", "C": "\033[92m"}
RESET = "\033[0m"
ENERGY_CHARS = {5: "▓▓▓▓▓", 4: "▓▓▓▓░", 3: "▓▓▓░░", 2: "▓▓░░░", 1: "▓░░░░"}


def pos_to_xy(pos: int, track_length: int) -> tuple[int, int]:
    """Converts a 1D position to (segment/row, column) for serpentine rendering."""
    seg_len = track_length // 3
    segment = min(pos // seg_len, 2)
    offset = pos % seg_len
    col = offset if segment % 2 == 0 else seg_len - 1 - offset
    return (segment, col)


def render(state: RaceState) -> str:
    """Returns a complete ASCII frame of the race state."""
    seg_len = state.track_length // 3
    width = seg_len  # number of columns per segment

    # Grid: 3 segments × width columns, each cell = list of cyclists
    cell_map: dict[tuple[int, int], list[Cyclist]] = {}
    for c in state.cyclists:
        if c.pos >= state.track_length:
            continue
        key = pos_to_xy(c.pos, state.track_length)
        cell_map.setdefault(key, []).append(c)

    # Max cyclists in a single cell (at least 1 for layout)
    max_occ = max((len(v) for v in cell_map.values()), default=1)

    def cyclist_cell(row_idx: int, col: int, layer: int) -> str:
        cyclists_here = cell_map.get((row_idx, col), [])
        if layer < len(cyclists_here):
            c = cyclists_here[layer]
            color = TEAM_COLORS.get(c.team, "")
            return f"{color}{c.id}{RESET}"
        return " · " if layer == 0 and not cyclists_here else "   "

    def energy_cell(row_idx: int, col: int) -> str:
        cyclists_here = cell_map.get((row_idx, col), [])
        if cyclists_here:
            c = cyclists_here[0]
            color = TEAM_COLORS.get(c.team, "")
            return f"{color}{ENERGY_CHARS[c.energy]}{RESET}"
        return "   "

    def render_cyclist_row(row_idx: int, layer: int) -> str:
        return " ".join(cyclist_cell(row_idx, col, layer) for col in range(width))

    def render_energy_row(row_idx: int) -> str:
        return " ".join(energy_cell(row_idx, col) for col in range(width))

    sep = "═" * (width * 4)
    corner_r = "┓"

    lines = []
    lines.append(f"{'─'*60}")
    lines.append(f"  🚴 ASYNC RACE   Tick {state.tick:>3}   Finished: {len(state.finished)}/9")
    lines.append(f"{'─'*60}")

    # Segment 0 (top, left→right)
    lines.append(f"[S]━╔{sep}╗━━━{corner_r}")
    for layer in range(max_occ):
        lines.append(f"   ║ {render_cyclist_row(0, layer)} ║   ┃")
    lines.append(f"   ║ {render_energy_row(0)} ║   ┃")
    lines.append(f"   ╚{sep}╝   ┃")

    # Segment 1 (middle, right→left)
    lines.append(f"   ╔{sep}╗   ┃")
    for layer in range(max_occ):
        connector = "━━━┛" if layer == 0 else "   ┃"
        lines.append(f"   ║ {render_cyclist_row(1, layer)} ║{connector}")
    lines.append(f"   ║ {render_energy_row(1)} ║")
    lines.append(f"┏━━╚{sep}╝")
    lines.append(f"┃")

    # Segment 2 (bottom, left→right)
    lines.append(f"┃  ╔{sep}╗")
    for layer in range(max_occ):
        prefix = "┗━━" if layer == 0 else "   "
        connector = "━━━[F]" if layer == 0 else "      "
        lines.append(f"{prefix}║ {render_cyclist_row(2, layer)} ║{connector}")
    lines.append(f"   ║ {render_energy_row(2)} ║")
    lines.append(f"   ╚{sep}╝")

    # Scoreboard
    lines.append(f"{'─'*60}")
    for team, color in TEAM_COLORS.items():
        team_cyclists = [c for c in state.cyclists if c.team == team]
        if not team_cyclists:
            continue
        row_parts = []
        for c in sorted(team_cyclists, key=lambda x: x.id):
            finished_mark = "✓" if c.id in state.finished else " "
            row_parts.append(
                f"{color}{c.id}{RESET}{finished_mark} {color}{ENERGY_CHARS[c.energy]}{RESET} #{c.pos:>2}"
            )
        lines.append(f"  ■ {color}{team}{RESET}  " + "   ".join(row_parts))

    return "\n".join(lines)


# =============================================================================
# MAIN LOOP — helpers
# =============================================================================

def validate_config(config: dict) -> None:
    """Validates config structure. Raises ValueError if invalid."""
    if "track_length" not in config:
        raise ValueError("Missing key 'track_length' in config")
    tl = config["track_length"]
    if not isinstance(tl, int) or isinstance(tl, bool) or not (20 <= tl <= 200):
        raise ValueError(f"track_length must be an integer between 20 and 200, got: {tl}")

    teams = config.get("teams", [])
    if len(teams) != 3:
        raise ValueError(f"Config must have exactly 3 teams, got: {len(teams)}")

    rider_counts = []
    for team in teams:
        if "name" not in team:
            raise ValueError(f"A team is missing the 'name' field")
        riders = team.get("riders", [])
        if len(riders) < 1:
            raise ValueError(f"Team {team.get('name')} must have at least 1 rider")
        rider_counts.append(len(riders))
        for rider in riders:
            if "id" not in rider:
                raise ValueError(f"A cyclist in team {team['name']} is missing the 'id' field")
            e = rider.get("energy", 0)
            if not isinstance(e, int) or isinstance(e, bool) or not (1 <= e <= 5):
                raise ValueError(
                    f"energy for {rider.get('id')} must be between 1 and 5, got: {e}"
                )
    if len(set(rider_counts)) > 1:
        counts_str = ", ".join(
            f"{t.get('name')}: {n}" for t, n in zip(teams, rider_counts)
        )
        raise ValueError(f"All teams must have the same number of riders ({counts_str})")


def load_config(path: str) -> dict:
    """Loads and validates a race_config.json file. Raises FileNotFoundError or ValueError."""
    with open(path) as f:
        config = json.load(f)
    validate_config(config)
    return config


def init_race_from_config(config: dict) -> RaceState:
    """Creates initial state from a validated JSON config.
    Starting positions are shuffled randomly."""
    all_riders: list[tuple[str, dict]] = []
    for team_data in config["teams"]:
        for rider in team_data["riders"]:
            all_riders.append((team_data["name"], rider))
    random.shuffle(all_riders)

    cyclists = []
    for pos, (team_name, rider) in enumerate(all_riders):
        cyclists.append(Cyclist(
            id=rider["id"],
            team=team_name,
            pos=pos,
            energy=rider["energy"],
            potion_used=False,
        ))
    cyclists.sort(key=lambda c: c.pos, reverse=True)
    return RaceState(
        track_length=config["track_length"],
        cyclists=cyclists,
        tick=0,
        finished=[],
    )


def init_race(track_length: int, teams: list[str], riders_per_team: int) -> RaceState:
    """Creates initial state. Cyclists placed at positions 0..N-1, random order."""
    all_riders = [
        (team, i)
        for team in teams
        for i in range(1, riders_per_team + 1)
    ]
    random.shuffle(all_riders)
    cyclists = [
        Cyclist(id=f"{team}{i}", team=team, pos=pos, energy=5, potion_used=False)
        for pos, (team, i) in enumerate(all_riders)
    ]
    cyclists.sort(key=lambda c: c.pos, reverse=True)
    return RaceState(track_length=track_length, cyclists=cyclists, tick=0, finished=[])


def race_over(state: RaceState) -> bool:
    """Race is over when one team has all 3 cyclists at the finish."""
    teams = {c.team for c in state.cyclists}
    for team in teams:
        team_ids = [c.id for c in state.cyclists if c.team == team]
        if all(cid in state.finished for cid in team_ids):
            return True
    return False


def winner(state: RaceState) -> str:
    """Returns the winning team (the one whose all cyclists finished first)."""
    teams = {c.team for c in state.cyclists}
    for team in teams:
        team_ids = [c.id for c in state.cyclists if c.team == team]
        if all(cid in state.finished for cid in team_ids):
            return team
    return ""


def write_results(
    state: RaceState,
    decisions_log: list[dict],
    path: str,
    state_log: list[dict] | None = None,
) -> None:
    """Writes race results to JSON."""
    initial_energies = {c.id: c.energy for c in state.cyclists}
    data = {
        "winner": winner(state),
        "ticks": state.tick,
        "finished_order": list(state.finished),
        "config_summary": {
            "track_length": state.track_length,
            "initial_energies": initial_energies,
        },
        "decisions_log": decisions_log,
    }
    if state_log is not None:
        data["state_log"] = state_log
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def parse_args() -> argparse.Namespace:
    """Parses CLI arguments."""
    parser = argparse.ArgumentParser(description="Cycling Race Async Simulation")
    parser.add_argument(
        "--config",
        default="race_config.json",
        help="Path to config file (default: race_config.json)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to JSON results file (optional)",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="CI mode: suppresses interactive prompts and pauses",
    )
    return parser.parse_args()


async def main() -> None:
    """
    Main race loop.

    EDUCATIONAL NOTE — The async/sync flow:
    1. orchestrator()  [ASYNC]  → 9 Claude calls in parallel
    2. resolve()       [SYNC]   → deterministic engine, no network calls
    3. render()        [SYNC]   → ASCII display
    4. Repeat until finished

    The async/sync separation is intentional:
    - Async code handles network latency (Claude agents)
    - Sync code handles game logic (no race conditions possible)
    """
    args = parse_args()

    # Load config (raises FileNotFoundError or ValueError if invalid)
    config = load_config(args.config)
    state = init_race_from_config(config)

    if not args.no_interactive:
        print("\n🚴 RACE STARTING 🚴\n")
        print(render(state))
        input("\nPress Enter to start the race...")

    decisions_log: list[dict] = []
    state_log: list[dict] = []

    def _snapshot(s: RaceState) -> dict:
        return {
            "tick": s.tick,
            "cyclists": [
                {"id": c.id, "team": c.team, "pos": c.pos, "energy": c.energy}
                for c in s.cyclists
            ],
        }

    state_log.append(_snapshot(state))

    while not race_over(state):
        if not args.no_interactive:
            print(f"\n{'─'*60}")
            print(f"⏳ Tick {state.tick + 1} — agents deciding...")

        # ASYNC: all sub-agents decide in parallel
        actions = await orchestrator(state)
        decisions_log.append({"tick": state.tick, "decisions": dict(actions)})

        if not args.no_interactive:
            print("  Decisions: " + "  ".join(
                f"{cid}:{action}" for cid, action in sorted(actions.items())
            ))

        # SYNC: engine resolves conflicts
        state = resolve(state, actions)
        state_log.append(_snapshot(state))

        if not args.no_interactive:
            print(render(state))
            await asyncio.sleep(0.3)

    if args.output:
        write_results(state, decisions_log, args.output, state_log)

    if not args.no_interactive:
        print(f"\n{'═'*60}")
        print(f"🏆 WINNER: Team {winner(state)}!")
        print(f"Finishing order: {', '.join(state.finished)}")
        print(f"Race completed in {state.tick} ticks.")
        print(f"{'═'*60}\n")
    else:
        print(f"winner={winner(state)} ticks={state.tick}")


if __name__ == "__main__":
    asyncio.run(main())
