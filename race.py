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
