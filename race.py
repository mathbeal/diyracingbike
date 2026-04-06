# -*- coding: utf-8 -*-
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
# AGENTS
# =============================================================================

_VALID_ACTIONS: set[str] = {"advance", "slow", "draft", "potion", "wait"}


def build_prompt(cyclist: Cyclist, state: RaceState) -> str:
    """Construit le prompt envoyé à Claude pour ce cycliste."""
    others = [c for c in state.cyclists if c.id != cyclist.id]

    # Cyclistes devant (dans un rayon de 5 cases)
    ahead = [c for c in others if 0 < c.pos - cyclist.pos <= 5]
    ahead_str = ", ".join(f"{c.id}(équipe {c.team}) à {c.pos - cyclist.pos} case(s)" for c in ahead)

    # Cyclistes derrière (dans un rayon de 5 cases)
    behind = [c for c in others if 0 < cyclist.pos - c.pos <= 5]
    behind_str = ", ".join(f"{c.id}(équipe {c.team}) à {cyclist.pos - c.pos} case(s)" for c in behind)

    # Coéquipiers
    teammates = [c for c in state.cyclists if c.team == cyclist.team and c.id != cyclist.id]
    team_str = "  ".join(f"{c.id}:#{c.pos}" for c in teammates)

    potion_status = "déjà utilisée" if cyclist.potion_used else "disponible"

    return f"""Tu es le cycliste {cyclist.id} (équipe {cyclist.team}).
Tick {state.tick} | Position #{cyclist.pos}/{state.track_length} | Énergie: {cyclist.energy}/5

Devant toi (≤5 cases): {ahead_str or "personne"}
Derrière toi (≤5 cases): {behind_str or "personne"}
Coéquipiers: {team_str}
Potion: {potion_status}

Stratégie: économise ton énergie en te mettant en roue (draft) quand possible.
Si tu es en tête et épuisé, ralentis (slow) pour laisser un coéquipier passer.
Utilise la potion au bon moment (sprint final ou pour remonter).

Réponds UNIQUEMENT par un seul mot parmi: advance | slow | draft | potion | wait"""


def parse_action(text: str) -> Action:
    """
    Extrait une action valide du texte retourné par Claude.
    Fallback sur "advance" si aucun mot valide trouvé.
    """
    text = text.strip().lower()
    # Cherche un mot valide dans le texte (Claude peut répondre "I choose draft")
    for word in text.split():
        clean = word.strip(".,!?:;\"'")
        if clean in _VALID_ACTIONS:
            return clean  # type: ignore
    return "advance"  # fallback


# Client Anthropic (singleton)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))


async def cyclist_agent(cyclist: Cyclist, state: RaceState) -> tuple[str, Action]:
    """
    Sous-agent : appelle Claude pour décider de l'action du cycliste.

    POINT PÉDAGOGIQUE :
    - Le SDK Anthropic Python est SYNCHRONE (client.messages.create bloque le thread)
    - asyncio.to_thread() l'exécute dans un thread pool → ne bloque pas la boucle async
    - Chaque cyclist_agent est une coroutine indépendante
    """
    prompt = build_prompt(cyclist, state)

    response = await asyncio.to_thread(
        client.messages.create,
        model="claude-haiku-4-5-20251001",
        max_tokens=10,  # On veut juste un mot → latence minimale
        messages=[{"role": "user", "content": prompt}],
    )

    action = parse_action(response.content[0].text)
    return (cyclist.id, action)


async def orchestrator(state: RaceState) -> dict[str, Action]:
    """
    Orchestrateur : lance tous les agents en parallèle avec asyncio.gather().

    POINT PÉDAGOGIQUE :
    - asyncio.gather(*tasks) démarre toutes les coroutines SIMULTANÉMENT
    - On attend que la PLUS LENTE ait répondu (pas la plus rapide)
    - return_exceptions=True : un agent qui plante ne bloque pas les autres
    - Le temps total ≈ max(latences individuelles), pas leur somme
    """
    active = [c for c in state.cyclists if c.id not in state.finished]

    # Crée les coroutines (pas encore lancées)
    tasks = [cyclist_agent(c, state) for c in active]

    # Lance TOUT en parallèle — c'est ici que la magie async opère
    results = await asyncio.gather(*tasks, return_exceptions=True)

    actions: dict[str, Action] = {}
    for i, result in enumerate(results):
        cyclist = active[i]
        if isinstance(result, Exception):
            # Fallback si Claude échoue pour ce cycliste
            print(f"  ⚠ Agent {cyclist.id} a échoué ({result}), fallback: advance")
            actions[cyclist.id] = "advance"
        else:
            cyclist_id, action = result
            actions[cyclist_id] = action

    return actions


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
# RENDU
# =============================================================================

TEAM_COLORS = {"A": "\033[94m", "B": "\033[93m", "C": "\033[92m"}
RESET = "\033[0m"
ENERGY_CHARS = {5: "▓▓▓▓▓", 4: "▓▓▓▓░", 3: "▓▓▓░░", 2: "▓▓░░░", 1: "▓░░░░"}


def pos_to_xy(pos: int, track_length: int) -> tuple[int, int]:
    """Convertit une position 1D en (segment/row, colonne) pour le rendu serpentin."""
    seg_len = track_length // 3
    segment = min(pos // seg_len, 2)
    offset = pos % seg_len
    col = offset if segment % 2 == 0 else seg_len - 1 - offset
    return (segment, col)


def render(state: RaceState) -> str:
    """Retourne une frame ASCII complète de l'état de la course."""
    seg_len = state.track_length // 3
    width = seg_len  # nombre de colonnes par segment

    # Grille : 3 segments × width colonnes, chaque cellule = (cyclist_id | None)
    grid: list[list[str | None]] = [[None] * width for _ in range(3)]
    energy_grid: list[list[str | None]] = [[None] * width for _ in range(3)]

    for c in state.cyclists:
        if c.pos >= state.track_length:
            continue
        row, col = pos_to_xy(c.pos, state.track_length)
        color = TEAM_COLORS.get(c.team, "")
        grid[row][col] = f"{color}{c.id}{RESET}"
        energy_grid[row][col] = f"{color}{ENERGY_CHARS[c.energy]}{RESET}"

    def render_row(row_idx: int) -> list[str]:
        """Retourne les deux lignes (cyclistes + énergie) d'un segment."""
        cells = grid[row_idx]
        ecells = energy_grid[row_idx]
        cyclist_line = " ".join(c if c else " · " for c in cells)
        energy_line  = " ".join(e if e else "   " for e in ecells)
        return [cyclist_line, energy_line]

    sep = "═" * (width * 4)
    corner_r = "┓"
    corner_l = "┗"

    lines = []
    lines.append(f"{'─'*60}")
    lines.append(f"  🚴 VÉLO ASYNC RACE   Tick {state.tick:>3}   Cyclistes finis: {len(state.finished)}/9")
    lines.append(f"{'─'*60}")

    # Segment 0 (haut, gauche→droite)
    s0_cyclists, s0_energy = render_row(0)
    lines.append(f"[S]━╔{sep}╗━━━{corner_r}")
    lines.append(f"   ║ {s0_cyclists} ║   ┃")
    lines.append(f"   ║ {s0_energy} ║   ┃")
    lines.append(f"   ╚{sep}╝   ┃")

    # Segment 1 (milieu, droite→gauche)
    s1_cyclists, s1_energy = render_row(1)
    lines.append(f"   ╔{sep}╗   ┃")
    lines.append(f"   ║ {s1_cyclists} ║━━━┛")
    lines.append(f"   ║ {s1_energy} ║")
    lines.append(f"┏━━╚{sep}╝")
    lines.append(f"┃")

    # Segment 2 (bas, gauche→droite)
    s2_cyclists, s2_energy = render_row(2)
    lines.append(f"┃  ╔{sep}╗")
    lines.append(f"┗━━║ {s2_cyclists} ║━━━[F]")
    lines.append(f"   ║ {s2_energy} ║")
    lines.append(f"   ╚{sep}╝")

    # Tableau de scores
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


async def main() -> None:
    """
    Boucle principale de la course.

    POINT PÉDAGOGIQUE — Le flux async/sync :
    1. orchestrator()  [ASYNC]  → 9 appels Claude en parallèle
    2. resolve()       [SYNC]   → moteur déterministe, sans appel réseau
    3. render()        [SYNC]   → affichage ASCII
    4. Recommencer jusqu'à la fin

    La séparation async/sync est intentionnelle :
    - Le code async gère la latence réseau (agents Claude)
    - Le code sync gère la logique de jeu (pas de race condition possible)
    """
    print("\n🚴 DÉMARRAGE DE LA COURSE 🚴\n")
    state = init_race(track_length=60, teams=["A", "B", "C"], riders_per_team=3)
    print(render(state))
    input("\nAppuyez sur Entrée pour lancer la course...")

    while not race_over(state):
        print(f"\n{'─'*60}")
        print(f"⏳ Tick {state.tick + 1} — agents en cours de décision...")

        # ASYNC : tous les sous-agents décident en parallèle
        actions = await orchestrator(state)

        # Afficher les décisions (pédagogique)
        print("  Décisions: " + "  ".join(
            f"{cid}:{action}" for cid, action in sorted(actions.items())
        ))

        # SYNC : le moteur résout les conflits
        state = resolve(state, actions)

        # Afficher la frame
        print(render(state))

        # Pause pour la lisibilité
        await asyncio.sleep(0.3)

    print(f"\n{'═'*60}")
    print(f"🏆 VAINQUEUR : Équipe {winner(state)} !")
    print(f"Classement des arrivées : {', '.join(state.finished)}")
    print(f"Course terminée en {state.tick} ticks.")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
