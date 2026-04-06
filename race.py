"""
Cycling Race — Agents Async avec Claude
Objectif didactique : comprendre asyncio.gather + pattern orchestrateur/sous-agents
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Literal
import asyncio
import os
from dotenv import load_dotenv
import anthropic

load_dotenv()

# =============================================================================
# MODÈLE
# =============================================================================

Action = Literal["advance", "slow", "draft", "potion", "wait"]


@dataclass
class Cyclist:
    id: str           # "A1", "B2", "C3"…
    team: str         # "A", "B", "C"
    pos: int          # position 1D (0=départ, track_length=arrivée)
    energy: int       # 1..5 (1=épuisé, 5=plein)
    potion_used: bool # True si potion déjà consommée


@dataclass
class RaceState:
    track_length: int        # nombre de cases (ex: 60)
    cyclists: list[Cyclist]  # triés par pos décroissante
    tick: int
    finished: list[str]      # ids dans l'ordre d'arrivée


# =============================================================================
# MOTEUR
# =============================================================================

_ACTION_STEP: dict[Action, int] = {
    "advance": 1,
    "slow":    0,
    "draft":   0,
    "potion":  1,
    "wait":    0,
}


def resolve(state: RaceState, actions: dict[str, Action]) -> RaceState:
    """
    Moteur synchrone et déterministe.
    1. Calcule les positions souhaitées
    2. Résout les collisions (le cycliste le plus avancé a priorité)
    3. Met à jour l'énergie selon les règles de draft
    4. Détecte les arrivées
    Retourne un NOUVEL état — ne mute jamais l'état existant.
    """
    # 1. Positions souhaitées
    desired: dict[str, int] = {}
    for c in state.cyclists:
        if c.id in state.finished:
            desired[c.id] = c.pos
            continue
        action = actions.get(c.id, "advance")
        desired[c.id] = c.pos + _ACTION_STEP[action]

    # 2. Résolution des collisions
    # Tri par position décroissante : le plus avancé a priorité
    sorted_cyclists = sorted(state.cyclists, key=lambda c: c.pos, reverse=True)
    occupied: set[int] = set()
    final_pos: dict[str, int] = {}

    for c in sorted_cyclists:
        p = desired[c.id]
        while p in occupied:
            p -= 1
        occupied.add(p)
        final_pos[c.id] = max(p, 0)

    # 3. Mise à jour énergie
    new_cyclists: list[Cyclist] = []
    for c in state.cyclists:
        if c.id in state.finished:
            new_cyclists.append(c)
            continue

        pos = final_pos[c.id]
        action = actions.get(c.id, "advance")

        # Quelqu'un à ≤2 cases devant ?
        anyone_ahead = any(
            final_pos[o.id] > pos and final_pos[o.id] <= pos + 2
            for o in state.cyclists if o.id != c.id
        )

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

    # 4. Arrivées
    new_finished = list(state.finished)
    # On ajoute dans l'ordre de position décroissante
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
# BOUCLE — helpers
# =============================================================================

def init_race(track_length: int, teams: list[str], riders_per_team: int) -> RaceState:
    """Crée l'état initial. Cyclistes placés aux positions 0..N-1 (échelonnés)."""
    cyclists = []
    pos = 0
    for team in teams:
        for i in range(1, riders_per_team + 1):
            cyclists.append(Cyclist(
                id=f"{team}{i}",
                team=team,
                pos=pos,
                energy=5,
                potion_used=False,
            ))
            pos += 1
    # Trier par pos décroissante (convention RaceState)
    cyclists.sort(key=lambda c: c.pos, reverse=True)
    return RaceState(track_length=track_length, cyclists=cyclists, tick=0, finished=[])


def race_over(state: RaceState) -> bool:
    """La course est finie quand une équipe a ses 3 cyclistes à l'arrivée."""
    teams = {c.team for c in state.cyclists}
    for team in teams:
        team_ids = [c.id for c in state.cyclists if c.team == team]
        if all(cid in state.finished for cid in team_ids):
            return True
    return False


def winner(state: RaceState) -> str:
    """Retourne l'équipe gagnante (celle dont tous les cyclistes sont finis en premier)."""
    teams = {c.team for c in state.cyclists}
    for team in teams:
        team_ids = [c.id for c in state.cyclists if c.team == team]
        if all(cid in state.finished for cid in team_ids):
            return team
    return ""
