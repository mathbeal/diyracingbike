# Cycling Race — Background Agents avec multiprocessing

**Date :** 2026-04-06  
**Objectif :** Projet didactique pour comprendre le pattern "background agents" avec des processus OS séparés, en complément de `race.py` (foreground async agents).

---

## 1. Vue d'ensemble

`race_bg.py` est un second fichier autonome qui rejoue la même course cycliste, mais avec une architecture radicalement différente : **13 processus OS indépendants** communiquant via `multiprocessing.Queue`.

Il réutilise intégralement la logique de `race.py` (modèle de données, moteur `resolve`, rendu ASCII, prompts Claude) — seuls les agents et l'orchestrateur changent.

**Différence fondamentale :**

| | `race.py` (foreground) | `race_bg.py` (background) |
|---|---|---|
| Parallélisme | Coopératif (asyncio) | Préemptif (OS scheduler) |
| Agents | Coroutines éphémères | Processus persistants |
| Communication | Appel de fonction | multiprocessing.Queue |
| Isolation | Mémoire partagée | Mémoire séparée par processus |
| Timeout | Non (attend tous) | Oui (deadline partagée 5s) |
| Spécialisation | Non (un agent = tout) | Oui (3 couches de spécialistes) |

---

## 2. Architecture — 3 couches de processus

### Couche 1 : Spécialistes (4 processus)

- **energy_worker** × 3 (un par équipe A, B, C) : analyse l'énergie de ses 3 cyclistes et publie une recommandation dans la `reco_queue` de l'équipe
- **strategist_worker** × 1 (global) : observe les positions de toutes les équipes et publie une tactique globale dans la `strategy_queue`

Ces processus tournent en continu. Ils reçoivent l'état à chaque tick et publient leur analyse dès qu'elle est prête.

### Couche 2 : Cyclistes (9 processus)

- **cyclist_worker** × 9 (un par cycliste) : reçoit l'état du tick, fait un `get(timeout=1s)` non-bloquant sur la `reco_queue` de son équipe et sur la `strategy_queue` globale, puis appelle Claude avec le contexte enrichi des recommandations.

### Couche 3 : Orchestrateur (processus principal)

- Envoie l'état à tous les 13 processus simultanément
- Collecte les 9 actions des cyclistes avec une deadline partagée (5s total)
- Fallback "advance" si un cycliste dépasse la deadline
- Appelle `resolve()` + `render()` en synchrone

---

## 3. Structure des queues

```
Orchestrateur
  │
  ├── energy_input_A ──→ energy_worker_A ──→ reco_queue_A ──→ [A1, A2, A3]
  ├── energy_input_B ──→ energy_worker_B ──→ reco_queue_B ──→ [B1, B2, B3]
  ├── energy_input_C ──→ energy_worker_C ──→ reco_queue_C ──→ [C1, C2, C3]
  ├── strategy_input ──→ strategist_worker ──→ strategy_queue ──→ [tous]
  │
  ├── cyclist_input_A1 ──→ cyclist_worker_A1 ──→ action_queue_A1
  ├── cyclist_input_A2 ──→ cyclist_worker_A2 ──→ action_queue_A2
  │   ...
  └── cyclist_input_C3 ──→ cyclist_worker_C3 ──→ action_queue_C3
```

**Total : 4 input queues spécialistes + 1 strategy_queue + 3 reco_queues + 9 input queues cyclistes + 9 action queues = 26 queues**

---

## 4. Modèle de données inter-processus

Les `multiprocessing.Queue` utilisent pickle pour la sérialisation. On passe des dicts Python simples (pas les dataclasses directement, pour éviter les problèmes de pickling avec les `Literal` types).

```python
# Orchestrateur → worker : état sérialisé
StateMsg = {
    "tick": int,
    "track_length": int,
    "cyclists": [{"id": str, "team": str, "pos": int, "energy": int, "potion_used": bool}],
    "finished": [str],
}

# Spécialiste énergie → cyclistes (via reco_queue)
EnergyReco = {
    "tick": int,
    "advice": str,  # texte libre ex: "A2 presque épuisé, se mettre en roue"
}

# Stratège → cyclistes (via strategy_queue)
StrategyReco = {
    "tick": int,
    "advice": str,  # texte libre ex: "Équipe B en tête, accélérer maintenant"
}

# Cycliste → orchestrateur (via action_queue)
ActionMsg = {
    "cyclist_id": str,
    "action": str,   # "advance"|"slow"|"draft"|"potion"|"wait"
}

# Signal d'arrêt
STOP = "STOP"
```

---

## 5. Workers — implémentation

### energy_worker

```python
def energy_worker(team: str, input_q: Queue, reco_q: Queue) -> None:
    """
    Analyse l'énergie de l'équipe et publie une recommandation.
    Tourne dans son propre processus — ne partage rien avec le reste.
    """
    client = anthropic.Anthropic()

    while True:
        msg = input_q.get()
        if msg == STOP:
            break

        state_data = msg
        team_cyclists = [c for c in state_data["cyclists"] if c["team"] == team]

        prompt = f"""Tu es l'analyste énergie de l'équipe {team}.
Cyclistes: {team_cyclists}
En une phrase, donne un conseil de gestion d'énergie pour ce tick."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        reco_q.put({"tick": state_data["tick"], "advice": response.content[0].text})
```

### strategist_worker

```python
def strategist_worker(input_q: Queue, strategy_q: Queue) -> None:
    """Analyse la course globale et publie une tactique."""
    client = anthropic.Anthropic()

    while True:
        msg = input_q.get()
        if msg == STOP:
            break

        state_data = msg
        leaders = sorted(state_data["cyclists"], key=lambda c: c["pos"], reverse=True)[:3]

        prompt = f"""Tu es le stratège de course. Tick {state_data["tick"]}.
Leaders: {leaders}
En une phrase, donne une tactique globale."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        strategy_q.put({"tick": state_data["tick"], "advice": response.content[0].text})
```

### cyclist_worker

```python
def cyclist_worker(
    cyclist_id: str, team: str,
    input_q: Queue, output_q: Queue,
    reco_q: Queue, strategy_q: Queue,
) -> None:
    """Décide de l'action en consultant les recommandations des spécialistes."""
    client = anthropic.Anthropic()

    while True:
        msg = input_q.get()
        if msg == STOP:
            break

        state = deserialize_state(msg)
        cyclist = next(c for c in state.cyclists if c.id == cyclist_id)

        # Consulter les spécialistes (non-bloquant — si pas encore dispo, on continue sans)
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
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        action = parse_action(response.content[0].text)
        output_q.put({"cyclist_id": cyclist_id, "action": action})
```

---

## 6. Orchestrateur et boucle principale

```python
@dataclass
class AgentProcess:
    process: multiprocessing.Process
    input_q: Queue
    output_q: Queue      # None pour les spécialistes
    cyclist_id: str      # "" pour les spécialistes

@dataclass
class SpecialistProcess:
    process: multiprocessing.Process
    input_q: Queue

def spawn_all(state: RaceState) -> tuple[dict, dict, Queue, list[Queue]]:
    """Lance les 13 processus background."""
    # strategy_q : partagée entre le stratège (écriture) et tous les cyclistes (lecture)
    strategy_q = multiprocessing.Queue()
    reco_queues = {}  # team → Queue

    specialists = {}
    # 1 stratège
    sq_input = multiprocessing.Queue()
    p = multiprocessing.Process(target=strategist_worker, args=(sq_input, strategy_q), daemon=True)
    p.start()
    specialists["strategist"] = SpecialistProcess(p, sq_input)

    # 3 energy workers
    for team in ["A", "B", "C"]:
        rq = multiprocessing.Queue()
        reco_queues[team] = rq
        eq_input = multiprocessing.Queue()
        p = multiprocessing.Process(target=energy_worker, args=(team, eq_input, rq), daemon=True)
        p.start()
        specialists[f"energy_{team}"] = SpecialistProcess(p, eq_input)

    # 9 cyclist workers
    agents = {}
    for c in state.cyclists:
        cq_input  = multiprocessing.Queue()
        cq_output = multiprocessing.Queue()
        p = multiprocessing.Process(
            target=cyclist_worker,
            args=(c.id, c.team, cq_input, cq_output, reco_queues[c.team], strategy_q),
            daemon=True,
        )
        p.start()
        agents[c.id] = AgentProcess(p, cq_input, cq_output, c.id)

    return agents, specialists, strategy_q, reco_queues


def orchestrator_bg(state, agents, specialists) -> dict[str, Action]:
    msg = serialize_state(state)
    active = [c for c in state.cyclists if c.id not in state.finished]

    # Envoyer aux spécialistes
    for spec in specialists.values():
        spec.input_q.put(msg)

    # Envoyer aux cyclistes
    for c in active:
        agents[c.id].input_q.put(msg)

    # Collecter avec deadline partagée
    actions = {}
    deadline = time.monotonic() + 5.0
    for c in active:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            result = agents[c.id].output_q.get(timeout=remaining)
            actions[result["cyclist_id"]] = result["action"]
        except queue.Empty:
            print(f"  ⏰ {c.id} timeout → advance")
            actions[c.id] = "advance"
    return actions


def main():
    state = init_race(60, ["A","B","C"], 3)
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    print(f"✓ 13 processus lancés")

    while not race_over(state):
        actions = orchestrator_bg(state, agents, specialists)
        state   = resolve(state, actions)
        print(render(state))
        time.sleep(0.3)

    print(f"🏆 {winner(state)}")
    for ap in list(agents.values()) + list(specialists.values()):
        ap.input_q.put(STOP)
    for ap in list(agents.values()) + list(specialists.values()):
        ap.process.join(timeout=3)

if __name__ == "__main__":
    multiprocessing.set_start_method("spawn")
    main()
```

---

## 7. Nouveaux points pédagogiques vs `race.py`

| Concept | Ce qu'il enseigne |
|---|---|
| `multiprocessing.Process` | Vrai processus OS, mémoire isolée, PID distinct |
| `multiprocessing.Queue` | Canal inter-processus thread-safe et process-safe |
| `daemon=True` | Processus fils qui s'arrête avec le parent |
| `get_nowait()` | Lecture non-bloquante — consultation optionnelle |
| `deadline` partagée | Timeout réaliste sur N agents simultanés |
| `set_start_method("spawn")` | Nécessaire sur Windows/macOS pour éviter fork-safety issues |
| Agents spécialisés | Séparation des responsabilités entre agents |
| Queues partagées | N lecteurs sur 1 queue (strategy_q lue par 9 cyclistes) |

---

## 8. Structure des fichiers

```
diyracingbike/
├── race.py         # foreground async agents (existant)
└── race_bg.py      # background multiprocessing agents (nouveau)
```

`race_bg.py` importe depuis `race.py` : `resolve`, `render`, `init_race`, `race_over`, `winner`, `parse_action`, `Cyclist`, `RaceState`.

---

## 9. Limites connues et hypothèses

- **`strategy_q` partagée entre 9 lecteurs** : chaque cycliste consomme le message — il ne sera lu qu'une fois. Pour que tous lisent la même recommandation, on pourrait utiliser un dict partagé (`multiprocessing.Manager().dict()`) ou dupliquer la queue. Pour simplifier, on accepte que seul le premier cycliste à lire obtient la stratégie.
- **Pas de test unitaire** pour les workers (ils dépendent d'appels Claude réels). La validation est manuelle.
- **macOS/Windows** : `set_start_method("spawn")` requis. Sur Linux, "fork" est le défaut mais peut poser des problèmes avec anthropic SDK.
