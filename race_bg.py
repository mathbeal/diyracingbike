# -*- coding: utf-8 -*-
"""
Cycling Race — Background Agents with multiprocessing
Educational goal: understand multiprocessing.Process + inter-process Queue

Architecture:
  - 4 specialists : 1 global strategist + 3 energy analysts (one per team)
  - 9 cyclists    : one process per cyclist, consults the specialists
  - 1 orchestrator: main process, sends state, collects actions

Imports business logic from race.py (resolve, render, init_race, ...).
"""

import multiprocessing
import queue
import time
from dataclasses import dataclass
from typing import Any

import anthropic

from race import (
    Cyclist,
    RaceState,
    init_race,
    parse_action,
    race_over,
    render,
    resolve,
    winner,
)

# Stop signal sent through queues to cleanly shut down workers
STOP = "STOP"

# Model used by all workers (change here to update all at once)
_MODEL = "claude-haiku-4-5-20251001"


# =============================================================================
# SERIALIZATION
# (multiprocessing.Queue uses pickle; we pass dicts for reliability)
# =============================================================================

def serialize_state(state: RaceState) -> dict:
    """Converts a RaceState to a serializable dict (pickle-safe)."""
    return {
        "tick": state.tick,
        "track_length": state.track_length,
        "cyclists": [
            {
                "id": c.id,
                "team": c.team,
                "pos": c.pos,
                "energy": c.energy,
                "potion_used": c.potion_used,
            }
            for c in state.cyclists
        ],
        "finished": list(state.finished),
    }


def deserialize_state(data: dict) -> RaceState:
    """Reconstructs a RaceState from a dict."""
    cyclists = [
        Cyclist(
            id=c["id"],
            team=c["team"],
            pos=c["pos"],
            energy=c["energy"],
            potion_used=c["potion_used"],
        )
        for c in data["cyclists"]
    ]
    return RaceState(
        track_length=data["track_length"],
        cyclists=cyclists,
        tick=data["tick"],
        finished=list(data["finished"]),
    )


# =============================================================================
# PROCESS MANAGEMENT DATACLASSES
# =============================================================================

@dataclass
class SpecialistProcess:
    """Reference to a specialist process (energy analyst or strategist)."""
    process: multiprocessing.Process
    input_q: "multiprocessing.Queue[Any]"  # receives state dicts or STOP


@dataclass
class AgentProcess:
    """Reference to a cyclist process."""
    process: multiprocessing.Process
    input_q: "multiprocessing.Queue[Any]"   # receives state dicts or STOP
    output_q: "multiprocessing.Queue[Any]"  # emits action dicts
    cyclist_id: str


# =============================================================================
# SPECIALIST WORKERS
# (run in separate processes, make synchronous Claude calls)
# =============================================================================

def energy_worker(team: str, input_q: multiprocessing.Queue, reco_q: multiprocessing.Queue) -> None:
    """
    Analyses the energy of the team's 3 cyclists and publishes a recommendation.

    EDUCATIONAL NOTE:
    - Runs in its own OS process (distinct PID, isolated memory)
    - Uses the synchronous Anthropic SDK (no asyncio here)
    - Infinite loop until STOP signal
    """
    client = anthropic.Anthropic()

    while True:
        msg = input_q.get()   # blocking — waits for next state
        if msg == STOP:
            break

        team_cyclists = [c for c in msg["cyclists"] if c["team"] == team]
        prompt = (
            f"You are the energy analyst for team {team}.\n"
            f"Cyclists: {team_cyclists}\n"
            f"In one short sentence, give an energy management tip for this tick."
        )
        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=60,
                messages=[{"role": "user", "content": prompt}],
            )
            reco_q.put({"tick": msg["tick"], "advice": response.content[0].text})
        except Exception as e:
            print(f"  ⚠️  energy_worker {team} tick {msg['tick']} error: {e}", flush=True)
            # Publishes nothing — cyclists will decide without energy advice this tick


def strategist_worker(input_q: multiprocessing.Queue, strategy_q: multiprocessing.Queue) -> None:
    """
    Observes all team positions and publishes a global tactic.

    EDUCATIONAL NOTE:
    - strategy_q is read by 9 cyclists (FIFO Queue — only the first reader gets the message)
    - Intentional behavior for the exercise: see spec section 9
    """
    client = anthropic.Anthropic()

    while True:
        msg = input_q.get()
        if msg == STOP:
            break

        leaders = sorted(msg["cyclists"], key=lambda c: c["pos"], reverse=True)[:3]
        prompt = (
            f"You are the race strategist. Tick {msg['tick']}.\n"
            f"Leaders: {leaders}\n"
            f"In one short sentence, give a global tactic."
        )
        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=60,
                messages=[{"role": "user", "content": prompt}],
            )
            strategy_q.put({"tick": msg["tick"], "advice": response.content[0].text})
        except Exception as e:
            print(f"  ⚠️  strategist_worker tick {msg['tick']} error: {e}", flush=True)
            # Publishes nothing — cyclists decide without strategy advice this tick


# =============================================================================
# CYCLIST WORKER
# =============================================================================

def build_prompt_bg(
    cyclist: Cyclist,
    state: RaceState,
    energy_advice: str,
    strategy_advice: str,
) -> str:
    """Builds the prompt for a cyclist, incorporating specialist advice."""
    others = [c for c in state.cyclists if c.id != cyclist.id and c.id not in state.finished]
    ahead = sorted([c for c in others if c.pos > cyclist.pos], key=lambda c: c.pos)
    behind = sorted([c for c in others if c.pos < cyclist.pos], key=lambda c: c.pos, reverse=True)

    prompt = (
        f"You are cyclist {cyclist.id} (team {cyclist.team}).\n"
        f"Position: {cyclist.pos}/{state.track_length}. Energy: {cyclist.energy}/5.\n"
        f"Potion used: {'yes' if cyclist.potion_used else 'no'}.\n"
        f"Ahead of you: {[c.id for c in ahead]}.\n"
        f"Behind you: {[c.id for c in behind]}.\n"
    )

    if energy_advice:
        prompt += f"\nEnergy advice (team specialist): {energy_advice}"
    if strategy_advice:
        prompt += f"\nStrategy advice (global strategist): {strategy_advice}"

    prompt += (
        "\n\nAvailable actions:\n"
        "- advance: move forward 1 cell (costs 1 energy)\n"
        "- slow: move slowly (recovers +1 energy if > 1)\n"
        "- draft: stay in the slipstream of the cyclist ahead (+1 energy)\n"
        "- potion: unique +3 energy boost then advance\n"
        "- wait: stay in place, recover +1 energy\n"
        "\nReply with ONE word from: advance slow draft potion wait"
    )
    return prompt


def cyclist_worker(
    cyclist_id: str,
    team: str,
    input_q: multiprocessing.Queue,
    output_q: multiprocessing.Queue,
    reco_q: multiprocessing.Queue,
    strategy_q: multiprocessing.Queue,
) -> None:
    """
    Decides the cyclist's action by consulting available recommendations.

    EDUCATIONAL NOTE:
    - get_nowait() = non-blocking read: if specialists haven't responded yet,
      the cyclist still decides (with less information)
    - Illustrates fault tolerance: a missing agent does not block the others
    - reco_q is shared among the 3 cyclists of the team (FIFO Queue):
      only the first cyclist to call get_nowait() gets the energy advice.
      Intentional behavior — see spec section 9.
    """
    client = anthropic.Anthropic()

    while True:
        msg = input_q.get()   # blocking — waits for next state
        if msg == STOP:
            break

        state = deserialize_state(msg)
        cyclist = next(c for c in state.cyclists if c.id == cyclist_id)

        # Non-blocking specialist consultation
        # Note: tick of advice is not validated intentionally.
        # An advice from a previous tick is preferable to no advice at all
        # (tolerance to temporal lag between specialists and cyclists).
        energy_advice = ""
        strategy_advice = ""
        try:
            energy_advice = reco_q.get_nowait()["advice"]
        except queue.Empty:
            pass
        try:
            strategy_advice = strategy_q.get_nowait()["advice"]
        except queue.Empty:
            pass

        prompt = build_prompt_bg(cyclist, state, energy_advice, strategy_advice)
        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}],
            )
            action = parse_action(response.content[0].text)
        except Exception as e:
            print(f"  ⚠️  cyclist_worker {cyclist_id} tick {state.tick} error: {e}", flush=True)
            action = "advance"  # fallback
        output_q.put({"cyclist_id": cyclist_id, "action": action})


# =============================================================================
# ORCHESTRATOR AND MAIN LOOP
# =============================================================================

def spawn_all(
    state: RaceState,
) -> tuple[dict[str, "AgentProcess"], dict[str, "SpecialistProcess"], multiprocessing.Queue, dict[str, multiprocessing.Queue]]:
    """
    Launches all 13 background processes.

    Returns:
        agents       : cyclist_id → AgentProcess
        specialists  : name → SpecialistProcess
        strategy_q   : shared queue (written by strategist, read by cyclists)
        reco_queues  : team → Queue (written by energy worker, read by team cyclists)
    """
    strategy_q: multiprocessing.Queue = multiprocessing.Queue()
    reco_queues: dict[str, multiprocessing.Queue] = {}
    specialists: dict[str, SpecialistProcess] = {}

    # Global strategist (1 process)
    sq_input: multiprocessing.Queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=strategist_worker, args=(sq_input, strategy_q), daemon=True)
    p.start()
    specialists["strategist"] = SpecialistProcess(p, sq_input)

    # Energy analysts (3 processes, one per team)
    teams = list(dict.fromkeys(c.team for c in state.cyclists))
    for team in teams:
        rq: multiprocessing.Queue = multiprocessing.Queue()
        reco_queues[team] = rq
        eq_input: multiprocessing.Queue = multiprocessing.Queue()
        p = multiprocessing.Process(target=energy_worker, args=(team, eq_input, rq), daemon=True)
        p.start()
        specialists[f"energy_{team}"] = SpecialistProcess(p, eq_input)

    # Cyclists (9 processes)
    agents: dict[str, AgentProcess] = {}
    for c in state.cyclists:
        cq_input: multiprocessing.Queue = multiprocessing.Queue()
        cq_output: multiprocessing.Queue = multiprocessing.Queue()
        p = multiprocessing.Process(
            target=cyclist_worker,
            args=(c.id, c.team, cq_input, cq_output, reco_queues[c.team], strategy_q),
            daemon=True,
        )
        p.start()
        agents[c.id] = AgentProcess(p, cq_input, cq_output, c.id)

    return agents, specialists, strategy_q, reco_queues


def orchestrator_bg(
    state: RaceState,
    agents: dict[str, "AgentProcess"],
    specialists: dict[str, "SpecialistProcess"],
) -> dict[str, str]:
    """
    Sends state to all processes, collects actions with a shared deadline.

    EDUCATIONAL NOTE:
    - Shared deadline = all 9 agents have 5s total (not 5s each).
      The last agent to respond may have less than 1s.
    - If a cyclist exceeds the deadline: fallback "advance" (fault tolerance).
    """
    msg = serialize_state(state)
    active = [c for c in state.cyclists if c.id not in state.finished]

    # Send to specialists (they publish into reco_queues / strategy_q)
    for spec in specialists.values():
        spec.input_q.put(msg)

    # Send to active cyclists
    for c in active:
        agents[c.id].input_q.put(msg)

    # Collect with a shared 5-second deadline
    actions: dict[str, str] = {}
    deadline = time.monotonic() + 5.0
    for c in active:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            result = agents[c.id].output_q.get(timeout=remaining)
            actions[result["cyclist_id"]] = result["action"]
        except queue.Empty:
            print(f"  ⏰ {c.id} timeout → advance (fallback)", flush=True)
            actions[c.id] = "advance"

    return actions


def main() -> None:
    """
    Main loop — background agents version.

    EDUCATIONAL NOTE — Comparison with race.py:
    ┌─────────────────┬──────────────────────┬──────────────────────────┐
    │                 │ race.py (foreground)  │ race_bg.py (background)  │
    ├─────────────────┼──────────────────────┼──────────────────────────┤
    │ Parallelism     │ Cooperative (asyncio) │ Preemptive (OS scheduler)│
    │ Agents          │ Ephemeral coroutines  │ Persistent processes     │
    │ Communication   │ Function calls        │ multiprocessing.Queue    │
    │ Isolation       │ Shared memory         │ Separate memory          │
    │ Timeout         │ No                    │ Yes (5s deadline)        │
    │ Specialization  │ No                    │ Yes (3 layers)           │
    └─────────────────┴──────────────────────┴──────────────────────────┘
    """
    state = init_race(60, ["A", "B", "C"], 3)
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    print(f"✓ 13 processes launched (1 strategist + 3 energy workers + 9 cyclists)")
    print(render(state))

    while not race_over(state):
        actions = orchestrator_bg(state, agents, specialists)
        state = resolve(state, actions)
        print(render(state))
        time.sleep(0.3)

    print(f"\n🏆 WINNER: Team {winner(state)}!")

    # Clean shutdown of all workers
    all_workers = list(agents.values()) + list(specialists.values())
    for w in all_workers:
        w.input_q.put(STOP)
    for w in all_workers:
        w.process.join(timeout=3)


if __name__ == "__main__":
    # spawn required to avoid fork-safety issues with the Anthropic SDK
    # (default on macOS/Windows; preferable on Linux too)
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass  # Already configured
    main()
