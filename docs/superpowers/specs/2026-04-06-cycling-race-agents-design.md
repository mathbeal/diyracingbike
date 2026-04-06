# Cycling Race — Async Agents avec Claude

**Date :** 2026-04-06  
**Objectif :** Projet didactique pour comprendre comment implémenter des agents async avec le SDK Anthropic Python.

---

## 1. Vue d'ensemble

Simulation d'une course cycliste entre 3 équipes de 3 cyclistes chacune, affichée frame par frame dans le terminal. Chaque cycliste est piloté par un agent Claude indépendant. Un orchestrateur lance tous les agents en parallèle via `asyncio.gather()`, collecte leurs décisions, puis un moteur synchrone et déterministe résout les conflits et fait évoluer l'état de la course.

**Points pédagogiques clés :**
- `asyncio.gather()` pour lancer N agents en parallèle
- `asyncio.to_thread()` pour wrapper un SDK bloquant (Anthropic) en coroutine
- Séparation stricte entre code async (agents) et code synchrone (moteur de jeu)
- Gestion des erreurs avec `return_exceptions=True`

---

## 2. Stack technique

- **Langage :** Python 3.11+
- **Async :** `asyncio` stdlib
- **SDK Claude :** `anthropic` (Python SDK officiel)
- **Modèle :** `claude-haiku-4-5-20251001` (rapide, peu cher, max_tokens=10)
- **Rendu :** ASCII terminal (bibliothèque standard uniquement, optionnellement `rich`)
- **Fichier unique :** `race.py` (~300 lignes, sections commentées)

---

## 3. Architecture

```
race.py
│
├── # === MODÈLE ===
│   ├── Action         — Literal["advance","slow","draft","potion","wait"]
│   ├── Cyclist        — dataclass: id, team, pos, energy, potion_used
│   └── RaceState      — dataclass: track_length, cyclists, tick, finished
│
├── # === AGENTS ===
│   ├── build_prompt(cyclist, state) → str
│   ├── parse_action(text) → Action
│   ├── cyclist_agent(cyclist, state) → (id, Action)   [coroutine]
│   └── orchestrator(state) → dict[str, Action]        [asyncio.gather]
│
├── # === MOTEUR ===
│   └── resolve(state, actions) → RaceState            [pur, synchrone]
│
├── # === RENDU ===
│   ├── pos_to_xy(pos, track_length) → (row, col)
│   └── render(state) → str                            [ASCII serpentin]
│
└── # === BOUCLE ===
    ├── init_race(track_length, teams, riders_per_team) → RaceState
    ├── race_over(state) → bool
    ├── winner(state) → str
    └── main()                                          [asyncio.run]
```

---

## 4. Modèle de données

```python
Action = Literal["advance", "slow", "draft", "potion", "wait"]

@dataclass
class Cyclist:
    id: str           # "A1", "A2", "B1"…
    team: str         # "A", "B", "C"
    pos: int          # position 1D sur la piste (0 = départ, N = arrivée)
    energy: int       # 1..5 (1=épuisé, 5=plein)
    potion_used: bool # True si potion déjà consommée

@dataclass
class RaceState:
    track_length: int        # nombre de cases (ex: 60)
    cyclists: list[Cyclist]  # triés par pos décroissante
    tick: int                # numéro du tick courant
    finished: list[str]      # ids des cyclistes ayant franchi l'arrivée, dans l'ordre
```

---

## 5. Agents async

### Sous-agent cycliste

```python
async def cyclist_agent(cyclist: Cyclist, state: RaceState) -> tuple[str, Action]:
    prompt = build_prompt(cyclist, state)
    response = await asyncio.to_thread(   # SDK bloquant → thread
        client.messages.create,
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}]
    )
    action = parse_action(response.content[0].text)
    return (cyclist.id, action)
```

**Prompt reçu par chaque agent :**
```
Tu es le cycliste A1. Tick 14/50.
Position : 32/60. Énergie : ▓▓▓▓░ (4/5).
Devant toi : B2 à 1 case. Derrière toi : C1 à 3 cases.
Équipe : A1(toi/#32) A2(#28) A3(#25).
Adversaires leaders : B1(#35) C1(#29).
Potion : disponible.
Réponds UNIQUEMENT par un mot : advance | slow | draft | potion | wait
```

### Orchestrateur

```python
async def orchestrator(state: RaceState) -> dict[str, Action]:
    active = [c for c in state.cyclists if c.id not in state.finished]
    tasks = [cyclist_agent(c, state) for c in active]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    actions = {}
    for result in results:
        if isinstance(result, Exception):
            actions[...] = "advance"  # fallback si Claude échoue
        else:
            cyclist_id, action = result
            actions[cyclist_id] = action
    return actions
```

---

## 6. Moteur de résolution (synchrone)

`resolve(state, actions) → RaceState` — pur et sans effet de bord.

**Algorithme :**

1. **Positions souhaitées** : chaque action est convertie en déplacement (`advance`→+1, `slow`/`draft`/`wait`→0, `potion`→+1)
2. **Résolution des collisions** : tri par position décroissante (le cycliste le plus avancé a priorité) ; si une case est occupée, on recule d'1 jusqu'à trouver une case libre. Garantit zéro superposition.
3. **Mise à jour de l'énergie** :
   - En tête (personne dans un rayon de 2 cases devant) → `-1`
   - En roue (quelqu'un à ≤2 cases devant) → `+1`
   - `potion` non utilisée → `+3` ce tick
   - Énergie clampée à `[1, 5]` (minimum 1 : un cycliste épuisé avance quand même)
4. **Détection d'arrivée** : tout cycliste dont `pos >= track_length` est ajouté à `finished`

---

## 7. Rendu ASCII — piste serpentin

La piste est un tableau 1D de `track_length` cases découpé en 3 segments affichés en serpentin :

- **Segment 0** (pos 0..N/3) : gauche → droite, ligne du haut — départ [S]
- **Segment 1** (pos N/3..2N/3) : droite → gauche, ligne du milieu — virage à 180°
- **Segment 2** (pos 2N/3..N) : gauche → droite, ligne du bas — arrivée [F]

```python
def pos_to_xy(pos: int, track_length: int) -> tuple[int, int]:
    seg_len = track_length // 3
    segment = min(pos // seg_len, 2)
    offset  = pos % seg_len
    row = segment
    col = offset if segment % 2 == 0 else seg_len - 1 - offset
    return (row, col)
```

Chaque cycliste est affiché à sa colonne exacte. Les cases vides affichent `·`. L'énergie est affichée sur la ligne du dessous (`▓▓▓░░`). Les virages sont rendus avec `┓`/`┗`.

**Format d'une frame :**
```
╔═══════════════════════════════════╗
[S]━║ ·   · A1  B1  C1  ·  B2  · ║━━━┓
    ║▓▓▓▓ ▓▓▓ ▓▓▓▓▓     ▓▓      ║   ┃
    ╚═══════════════════════════════════╝   ┃
    ╔═══════════════════════════════════╗   ┃
    ║ ·   ·  C2  ·  A2  ·   ·   · ║━━━┛
 ┏━━║                                   ║
 ┃  ╚═══════════════════════════════════╝
 ┃  ╔═══════════════════════════════════╗
 ┗━━║ ·  A3  ·  B3  ·  C3  ·   · ║━━━[F]
    ╚═══════════════════════════════════╝

■ A  A1 ▓▓▓▓░ #2   A2 ▓▓▓░░ #9   A3 ▓▓░░░ #17
■ B  B1 ▓▓▓░░ #3   B2 ▓▓░░░ #6   B3 ▓▓▓▓▓ #19
■ C  C1 ▓▓▓▓▓ #4   C2 ▓▓▓▓░ #12  C3 ▓▓▓░░ #21

▸ A1:draft   B1:advance  C1:advance
  A2:potion✨ B2:wait     C2:advance
  A3:slow    B3:advance  C3:draft
```

---

## 8. Boucle principale

```python
async def main():
    state = init_race(track_length=60, teams=["A","B","C"], riders_per_team=3)
    print(render(state))

    while not race_over(state):
        actions = await orchestrator(state)   # ASYNC : 9 agents en parallèle
        state   = resolve(state, actions)      # SYNC  : moteur déterministe
        print(render(state))
        await asyncio.sleep(0.5)

    print(f"\n🏆 Vainqueur : Équipe {winner(state)}")
    print(f"Classement final : {state.finished}")

asyncio.run(main())
```

---

## 9. Règles de jeu complètes

| Situation | Effet énergie |
|---|---|
| En tête (personne à ≤2 cases devant) | -1/tick |
| En roue (quelqu'un à ≤2 cases devant, même équipe adverse) | +1/tick |
| Action `slow` ou `wait` | +0 (neutre, s'additionne aux règles ci-dessus) |
| Action `potion` (usage unique) | +3 ce tick |
| Énergie minimum | 1 (jamais 0) |
| Énergie maximum | 5 |

**Condition de victoire :** L'équipe dont les 3 cyclistes franchissent `pos >= track_length` en premier.

---

## 10. Structure de fichiers

```
diyracingbike/
└── race.py          # tout le code, ~300 lignes
```

Variable d'environnement requise : `ANTHROPIC_API_KEY`.

---

## 11. Ce que ce projet enseigne

1. **`asyncio.gather()`** — lancer N coroutines en parallèle et attendre la plus lente
2. **`asyncio.to_thread()`** — rendre async un SDK synchrone sans le réécrire
3. **`return_exceptions=True`** — résilience : un agent qui tombe ne bloque pas les autres
4. **Séparation async/sync** — les agents ne mutent jamais l'état partagé ; seul `resolve()` le fait
5. **Pattern orchestrateur/sous-agents** — un coroutine centrale coordonne des workers indépendants
