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
