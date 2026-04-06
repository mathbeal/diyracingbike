# Cycling Race Async Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implémenter `race.py`, un simulateur de course cycliste ASCII piloté par 9 agents Claude async (3 équipes × 3 cyclistes), didactique pour comprendre `asyncio.gather` et le pattern orchestrateur/sous-agents.

**Architecture:** Un seul fichier `race.py` découpé en 5 sections commentées (MODÈLE, AGENTS, MOTEUR, RENDU, BOUCLE). Les agents déclarent des intentions via Claude Haiku, un moteur synchrone `resolve()` résout les conflits et mute l'état, le rendu ASCII affiche la piste serpentin frame par frame.

**Tech Stack:** Python 3.11+, `anthropic` SDK, `asyncio` stdlib, `pytest` + `pytest-asyncio` pour les tests.

---

## Structure des fichiers

```
diyracingbike/
├── race.py              # code principal (~300 lignes)
├── tests/
│   └── test_race.py     # tests unitaires
├── .env                 # ANTHROPIC_API_KEY (non commité)
├── .gitignore
└── requirements.txt
```

---

## Task 1 : Setup projet

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `tests/__init__.py`

- [ ] **Step 1 : Créer `requirements.txt`**

```
anthropic>=0.25.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
python-dotenv>=1.0.0
```

- [ ] **Step 2 : Créer `.gitignore`**

```
.env
__pycache__/
*.pyc
.pytest_cache/
.superpowers/
```

- [ ] **Step 3 : Installer les dépendances**

```bash
pip install -r requirements.txt
```

Sortie attendue : `Successfully installed anthropic-... pytest-...`

- [ ] **Step 4 : Créer `tests/__init__.py` vide**

```bash
mkdir -p tests && touch tests/__init__.py
```

- [ ] **Step 5 : Créer `.env` avec la clé API**

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

(Remplacer `sk-ant-...` par votre vraie clé)

- [ ] **Step 6 : Commit**

```bash
git add requirements.txt .gitignore tests/__init__.py
git commit -m "chore: setup project structure"
```

---

## Task 2 : Modèle de données

**Files:**
- Create: `race.py` (section MODÈLE uniquement)
- Create: `tests/test_race.py`

- [ ] **Step 1 : Écrire le test du modèle**

Créer `tests/test_race.py` :

```python
import pytest
from race import Cyclist, RaceState, Action

def test_cyclist_defaults():
    c = Cyclist(id="A1", team="A", pos=0, energy=5, potion_used=False)
    assert c.id == "A1"
    assert c.team == "A"
    assert c.pos == 0
    assert c.energy == 5
    assert c.potion_used is False

def test_race_state_defaults():
    c1 = Cyclist(id="A1", team="A", pos=0, energy=5, potion_used=False)
    state = RaceState(track_length=60, cyclists=[c1], tick=0, finished=[])
    assert state.track_length == 60
    assert len(state.cyclists) == 1
    assert state.tick == 0
    assert state.finished == []

def test_action_values():
    valid: Action = "advance"
    assert valid in ("advance", "slow", "draft", "potion", "wait")
```

- [ ] **Step 2 : Vérifier que le test échoue**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `ERROR ... ModuleNotFoundError: No module named 'race'`

- [ ] **Step 3 : Créer `race.py` avec la section MODÈLE**

```python
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
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `3 passed`

- [ ] **Step 5 : Commit**

```bash
git add race.py tests/test_race.py
git commit -m "feat: add data model (Cyclist, RaceState, Action)"
```

---

## Task 3 : Initialisation de la course

**Files:**
- Modify: `race.py` (section BOUCLE — fonctions init)
- Modify: `tests/test_race.py`

- [ ] **Step 1 : Écrire le test**

Ajouter dans `tests/test_race.py` :

```python
from race import init_race, race_over, winner

def test_init_race_creates_correct_cyclists():
    state = init_race(track_length=60, teams=["A","B","C"], riders_per_team=3)
    assert len(state.cyclists) == 9
    assert state.tick == 0
    assert state.finished == []
    ids = [c.id for c in state.cyclists]
    assert "A1" in ids
    assert "B3" in ids
    assert "C2" in ids

def test_init_race_all_at_start():
    state = init_race(track_length=60, teams=["A","B","C"], riders_per_team=3)
    # Tous à des positions 0..8 (départ échelonné pour éviter superposition)
    positions = [c.pos for c in state.cyclists]
    assert len(set(positions)) == 9  # toutes différentes
    assert all(0 <= p < 9 for p in positions)

def test_race_not_over_at_start():
    state = init_race(track_length=60, teams=["A","B","C"], riders_per_team=3)
    assert race_over(state) is False

def test_winner_returns_team_when_all_finished():
    c1 = Cyclist(id="A1", team="A", pos=60, energy=3, potion_used=False)
    c2 = Cyclist(id="A2", team="A", pos=60, energy=3, potion_used=False)
    c3 = Cyclist(id="A3", team="A", pos=60, energy=3, potion_used=False)
    state = RaceState(track_length=60, cyclists=[c1,c2,c3], tick=20,
                      finished=["A1","A2","A3"])
    assert winner(state) == "A"
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `ImportError: cannot import name 'init_race'`

- [ ] **Step 3 : Implémenter dans `race.py`**

Ajouter à la fin de `race.py` :

```python
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
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `7 passed`

- [ ] **Step 5 : Commit**

```bash
git add race.py tests/test_race.py
git commit -m "feat: add init_race, race_over, winner"
```

---

## Task 4 : Moteur `resolve`

**Files:**
- Modify: `race.py` (section MOTEUR)
- Modify: `tests/test_race.py`

- [ ] **Step 1 : Écrire les tests**

Ajouter dans `tests/test_race.py` :

```python
from race import resolve

def _make_state(*cyclists):
    return RaceState(track_length=20, cyclists=list(cyclists), tick=0, finished=[])

def _c(id, team, pos, energy=5, potion_used=False):
    return Cyclist(id=id, team=team, pos=pos, energy=energy, potion_used=potion_used)

def test_resolve_advance_moves_forward():
    state = _make_state(_c("A1","A",5))
    new = resolve(state, {"A1": "advance"})
    assert new.cyclists[0].pos == 6

def test_resolve_wait_does_not_move():
    state = _make_state(_c("A1","A",5))
    new = resolve(state, {"A1": "wait"})
    assert new.cyclists[0].pos == 5

def test_resolve_no_superposition():
    # Deux cyclistes veulent avancer vers la même case
    state = _make_state(_c("A1","A",5), _c("B1","B",4))
    new = resolve(state, {"A1": "advance", "B1": "advance"})
    positions = [c.pos for c in new.cyclists]
    assert len(set(positions)) == 2  # pas de superposition

def test_resolve_energy_decreases_at_front():
    state = _make_state(_c("A1","A",10, energy=5))
    new = resolve(state, {"A1": "advance"})
    assert new.cyclists[0].energy == 4  # en tête → -1

def test_resolve_energy_increases_drafting():
    # A1 en tête, B1 juste derrière
    state = _make_state(_c("A1","A",10), _c("B1","B",9, energy=3))
    new = resolve(state, {"A1": "advance", "B1": "advance"})
    b1 = next(c for c in new.cyclists if c.id == "B1")
    assert b1.energy == 4  # en roue → +1

def test_resolve_energy_clamped_at_5():
    state = _make_state(_c("A1","A",10), _c("B1","B",9, energy=5))
    new = resolve(state, {"A1": "advance", "B1": "draft"})
    b1 = next(c for c in new.cyclists if c.id == "B1")
    assert b1.energy == 5  # pas dépassé 5

def test_resolve_energy_min_1():
    state = _make_state(_c("A1","A",10, energy=1))
    new = resolve(state, {"A1": "advance"})
    assert new.cyclists[0].energy == 1  # jamais en dessous de 1

def test_resolve_potion_adds_energy():
    state = _make_state(_c("A1","A",10, energy=2, potion_used=False))
    new = resolve(state, {"A1": "potion"})
    a1 = new.cyclists[0]
    assert a1.potion_used is True
    assert a1.energy == 5  # 2 -1(front) +3(potion) = 4, clamped... non: 2+3-1=4

def test_resolve_potion_already_used_no_effect():
    state = _make_state(_c("A1","A",10, energy=3, potion_used=True))
    new = resolve(state, {"A1": "potion"})
    a1 = new.cyclists[0]
    assert a1.energy == 2  # potion ignorée → -1 front seulement

def test_resolve_adds_to_finished():
    state = _make_state(_c("A1","A",19, energy=5))
    new = resolve(state, {"A1": "advance"})
    assert "A1" in new.finished

def test_resolve_tick_increments():
    state = _make_state(_c("A1","A",5))
    new = resolve(state, {"A1": "advance"})
    assert new.tick == 1
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `ImportError: cannot import name 'resolve'`

- [ ] **Step 3 : Implémenter `resolve` dans `race.py`**

Ajouter après le modèle, avant la section BOUCLE :

```python
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
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `18 passed`

> Note : `test_resolve_potion_adds_energy` : énergie = 2 - 1(front) + 3(potion) = 4. Si le cycliste est seul en tête, énergie finale = 4.

- [ ] **Step 5 : Commit**

```bash
git add race.py tests/test_race.py
git commit -m "feat: add resolve engine with collision resolution and energy rules"
```

---

## Task 5 : Rendu ASCII — `pos_to_xy` et `render`

**Files:**
- Modify: `race.py` (section RENDU)
- Modify: `tests/test_race.py`

- [ ] **Step 1 : Écrire les tests**

Ajouter dans `tests/test_race.py` :

```python
from race import pos_to_xy, render

def test_pos_to_xy_segment_0_left_to_right():
    # Piste de 30 cases, 3 segments de 10
    # Segment 0 : pos 0-9 → row 0, col 0-9
    assert pos_to_xy(0, 30) == (0, 0)
    assert pos_to_xy(5, 30) == (0, 5)
    assert pos_to_xy(9, 30) == (0, 9)

def test_pos_to_xy_segment_1_right_to_left():
    # Segment 1 : pos 10-19 → row 1, col 9-0 (inversé)
    assert pos_to_xy(10, 30) == (1, 9)
    assert pos_to_xy(15, 30) == (1, 4)
    assert pos_to_xy(19, 30) == (1, 0)

def test_pos_to_xy_segment_2_left_to_right():
    # Segment 2 : pos 20-29 → row 2, col 0-9
    assert pos_to_xy(20, 30) == (2, 0)
    assert pos_to_xy(25, 30) == (2, 5)
    assert pos_to_xy(29, 30) == (2, 9)

def test_render_returns_string():
    state = init_race(track_length=30, teams=["A","B"], riders_per_team=2)
    frame = render(state)
    assert isinstance(frame, str)
    assert len(frame) > 0

def test_render_contains_team_ids():
    state = init_race(track_length=30, teams=["A","B"], riders_per_team=2)
    frame = render(state)
    assert "A1" in frame
    assert "B1" in frame

def test_render_contains_start_and_finish():
    state = init_race(track_length=30, teams=["A","B"], riders_per_team=2)
    frame = render(state)
    assert "[S]" in frame
    assert "[F]" in frame
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `ImportError: cannot import name 'pos_to_xy'`

- [ ] **Step 3 : Implémenter la section RENDU dans `race.py`**

Ajouter après la section MOTEUR :

```python
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
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `24 passed`

- [ ] **Step 5 : Afficher visuellement un frame (vérification manuelle)**

```bash
python -c "
from race import init_race, render
state = init_race(60, ['A','B','C'], 3)
print(render(state))
"
```

La piste doit apparaître avec les 9 cyclistes bien positionnés sur les 3 segments.

- [ ] **Step 6 : Commit**

```bash
git add race.py tests/test_race.py
git commit -m "feat: add ASCII renderer with serpentine track layout"
```

---

## Task 6 : Prompt + parse_action

**Files:**
- Modify: `race.py` (section AGENTS — helpers)
- Modify: `tests/test_race.py`

- [ ] **Step 1 : Écrire les tests**

Ajouter dans `tests/test_race.py` :

```python
from race import build_prompt, parse_action

def test_build_prompt_contains_cyclist_id():
    state = init_race(60, ["A","B","C"], 3)
    c = state.cyclists[0]
    prompt = build_prompt(c, state)
    assert c.id in prompt

def test_build_prompt_contains_energy():
    state = init_race(60, ["A","B","C"], 3)
    c = state.cyclists[0]
    prompt = build_prompt(c, state)
    assert str(c.energy) in prompt

def test_build_prompt_contains_valid_actions():
    state = init_race(60, ["A","B","C"], 3)
    c = state.cyclists[0]
    prompt = build_prompt(c, state)
    assert "advance" in prompt
    assert "draft" in prompt
    assert "potion" in prompt

def test_build_prompt_shows_potion_used():
    state = init_race(60, ["A"], 1)
    c = state.cyclists[0]
    c.potion_used = True
    prompt = build_prompt(c, state)
    assert "utilisée" in prompt.lower() or "used" in prompt.lower()

def test_parse_action_valid():
    assert parse_action("advance") == "advance"
    assert parse_action("ADVANCE") == "advance"
    assert parse_action("  draft  ") == "draft"

def test_parse_action_fallback_on_invalid():
    # Si Claude répond n'importe quoi → fallback "advance"
    assert parse_action("je veux aller vite") == "advance"
    assert parse_action("") == "advance"
    assert parse_action("go go go") == "advance"

def test_parse_action_extracts_word_from_sentence():
    # Claude peut répondre "I choose draft" → on extrait "draft"
    assert parse_action("I choose draft") == "draft"
    assert parse_action("Ma décision: slow") == "slow"
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `ImportError: cannot import name 'build_prompt'`

- [ ] **Step 3 : Implémenter dans `race.py`**

Ajouter la section AGENTS avant la section MOTEUR :

```python
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
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `32 passed`

- [ ] **Step 5 : Commit**

```bash
git add race.py tests/test_race.py
git commit -m "feat: add build_prompt and parse_action for Claude agents"
```

---

## Task 7 : Agent cycliste + orchestrateur (async)

**Files:**
- Modify: `race.py` (section AGENTS — coroutines)
- Modify: `tests/test_race.py`

- [ ] **Step 1 : Écrire les tests**

Ajouter dans `tests/test_race.py` :

```python
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from race import cyclist_agent, orchestrator

def make_mock_response(text: str):
    """Crée un faux objet réponse Anthropic."""
    mock = MagicMock()
    mock.content = [MagicMock(text=text)]
    return mock

@pytest.mark.asyncio
async def test_cyclist_agent_returns_valid_action():
    state = init_race(60, ["A","B","C"], 3)
    cyclist = state.cyclists[0]

    with patch("race.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
        mock_thread.return_value = make_mock_response("advance")
        cid, action = await cyclist_agent(cyclist, state)

    assert cid == cyclist.id
    assert action == "advance"

@pytest.mark.asyncio
async def test_cyclist_agent_handles_bad_response():
    state = init_race(60, ["A","B","C"], 3)
    cyclist = state.cyclists[0]

    with patch("race.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
        mock_thread.return_value = make_mock_response("je vais très vite!")
        cid, action = await cyclist_agent(cyclist, state)

    assert action == "advance"  # fallback

@pytest.mark.asyncio
async def test_orchestrator_returns_action_for_each_cyclist():
    state = init_race(60, ["A","B","C"], 3)

    with patch("race.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
        mock_thread.return_value = make_mock_response("draft")
        actions = await orchestrator(state)

    assert len(actions) == 9
    for c in state.cyclists:
        assert c.id in actions
        assert actions[c.id] == "draft"

@pytest.mark.asyncio
async def test_orchestrator_handles_agent_exception():
    state = init_race(60, ["A","B","C"], 3)
    call_count = 0

    async def fake_to_thread(fn, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("API error")
        return make_mock_response("slow")

    with patch("race.asyncio.to_thread", side_effect=fake_to_thread):
        actions = await orchestrator(state)

    # Tous les cyclistes ont une action (fallback pour le premier)
    assert len(actions) == 9
    assert all(a in _VALID_ACTIONS for a in actions.values())
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `ImportError: cannot import name 'cyclist_agent'`

- [ ] **Step 3 : Implémenter dans `race.py` (ajouter après `parse_action`)**

```python
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
```

- [ ] **Step 4 : Ajouter `_VALID_ACTIONS` à l'import dans le test**

Vérifier que `tests/test_race.py` importe `_VALID_ACTIONS` :

```python
from race import (Cyclist, RaceState, Action, init_race, race_over, winner,
                  resolve, pos_to_xy, render, build_prompt, parse_action,
                  cyclist_agent, orchestrator, _VALID_ACTIONS)
```

- [ ] **Step 5 : Vérifier que les tests passent**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `36 passed`

- [ ] **Step 6 : Commit**

```bash
git add race.py tests/test_race.py
git commit -m "feat: add cyclist_agent coroutine and orchestrator with asyncio.gather"
```

---

## Task 8 : Boucle principale `main()`

**Files:**
- Modify: `race.py` (section BOUCLE — main)

Cette tâche n'a pas de test unitaire (la boucle dépend des appels Claude réels). On valide manuellement.

- [ ] **Step 1 : Ajouter `main()` à la fin de `race.py`**

```python
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
```

- [ ] **Step 2 : Vérifier la syntaxe**

```bash
python -c "import race; print('OK')"
```

Sortie attendue : `OK`

- [ ] **Step 3 : Lancer une course (avec clé API valide)**

```bash
python race.py
```

Observer :
- Les 9 agents sont lancés en parallèle à chaque tick
- La piste se redessine frame par frame
- Les décisions de chaque agent sont affichées
- La course se termine quand une équipe a ses 3 cyclistes à l'arrivée

- [ ] **Step 4 : Vérifier les tests complets une dernière fois**

```bash
pytest tests/test_race.py -v
```

Sortie attendue : `36 passed`

- [ ] **Step 5 : Commit final**

```bash
git add race.py
git commit -m "feat: add main loop — complete async cycling race simulation"
```

---

## Récapitulatif pédagogique

| Concept | Où dans le code | Ce qu'il enseigne |
|---|---|---|
| `asyncio.to_thread()` | `cyclist_agent()` | Wrapper un SDK bloquant en coroutine |
| `asyncio.gather(*tasks)` | `orchestrator()` | Paralléliser N agents indépendants |
| `return_exceptions=True` | `orchestrator()` | Résilience : un agent tombé ne bloque pas |
| Séparation async/sync | `main()` | Les agents ne mutent jamais l'état partagé |
| Pattern orchestrateur | `orchestrator()` | Une coroutine centrale coordonne des workers |
| Fallback d'action | `orchestrator()` | Robustesse face aux erreurs API |
