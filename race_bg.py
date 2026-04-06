# -*- coding: utf-8 -*-
"""
Cycling Race — Background Agents avec multiprocessing
Objectif didactique : comprendre multiprocessing.Process + Queue inter-processus

Architecture :
  - 4 spécialistes  : 1 stratège global + 3 analystes énergie (un par équipe)
  - 9 cyclistes     : un processus par cycliste, consulte les spécialistes
  - 1 orchestrateur : processus principal, envoie l'état, collecte les actions

Importe la logique métier depuis race.py (resolve, render, init_race, ...).
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

# Signal d'arrêt envoyé dans les queues pour arrêter proprement les workers
STOP = "STOP"

# Modèle utilisé par tous les workers (changer ici pour tous les mettre à jour)
_MODEL = "claude-haiku-4-5-20251001"


# =============================================================================
# SÉRIALISATION
# (multiprocessing.Queue utilise pickle ; on passe des dicts pour fiabilité)
# =============================================================================

def serialize_state(state: RaceState) -> dict:
    """Convertit un RaceState en dict sérialisable (pickle-safe)."""
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
    """Reconstruit un RaceState depuis un dict."""
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
# DATACLASSES DE GESTION DES PROCESSUS
# =============================================================================

@dataclass
class SpecialistProcess:
    """Référence vers un processus spécialiste (énergie ou stratège)."""
    process: multiprocessing.Process
    input_q: "multiprocessing.Queue[Any]"  # reçoit des dicts d'état ou STOP


@dataclass
class AgentProcess:
    """Référence vers un processus cycliste."""
    process: multiprocessing.Process
    input_q: "multiprocessing.Queue[Any]"   # reçoit des dicts d'état ou STOP
    output_q: "multiprocessing.Queue[Any]"  # émet des dicts d'action
    cyclist_id: str


# =============================================================================
# WORKERS SPÉCIALISTES
# (tournent dans des processus séparés, font des appels Claude synchrones)
# =============================================================================

def energy_worker(team: str, input_q: multiprocessing.Queue, reco_q: multiprocessing.Queue) -> None:
    """
    Analyse l'énergie des 3 cyclistes de l'équipe et publie une recommandation.

    POINT PÉDAGOGIQUE :
    - Tourne dans son propre processus OS (PID distinct, mémoire isolée)
    - Utilise le SDK Anthropic synchrone (pas d'asyncio ici)
    - Boucle infinie jusqu'au signal STOP
    """
    client = anthropic.Anthropic()

    while True:
        msg = input_q.get()   # bloquant — attend le prochain état
        if msg == STOP:
            break

        team_cyclists = [c for c in msg["cyclists"] if c["team"] == team]
        prompt = (
            f"Tu es l'analyste énergie de l'équipe {team}.\n"
            f"Cyclistes: {team_cyclists}\n"
            f"En une phrase courte, donne un conseil de gestion d'énergie pour ce tick."
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
            # Ne publie rien — les cyclistes liront sans conseil énergie ce tick


def strategist_worker(input_q: multiprocessing.Queue, strategy_q: multiprocessing.Queue) -> None:
    """
    Observe les positions de toutes les équipes et publie une tactique globale.

    POINT PÉDAGOGIQUE :
    - strategy_q est lue par 9 cyclistes (Queue FIFO — seul le premier lecteur obtient le message)
    - Comportement intentionnel pour l'exercice : voir spec section 9
    """
    client = anthropic.Anthropic()

    while True:
        msg = input_q.get()
        if msg == STOP:
            break

        leaders = sorted(msg["cyclists"], key=lambda c: c["pos"], reverse=True)[:3]
        prompt = (
            f"Tu es le stratège de course. Tick {msg['tick']}.\n"
            f"Leaders: {leaders}\n"
            f"En une phrase courte, donne une tactique globale."
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
            # Ne publie rien — les cyclistes décident sans conseil stratégie ce tick


# =============================================================================
# WORKER CYCLISTE
# =============================================================================

def build_prompt_bg(
    cyclist: Cyclist,
    state: RaceState,
    energy_advice: str,
    strategy_advice: str,
) -> str:
    """Construit le prompt pour un cycliste en intégrant les conseils des spécialistes."""
    others = [c for c in state.cyclists if c.id != cyclist.id and c.id not in state.finished]
    ahead = sorted([c for c in others if c.pos > cyclist.pos], key=lambda c: c.pos)
    behind = sorted([c for c in others if c.pos < cyclist.pos], key=lambda c: c.pos, reverse=True)

    prompt = (
        f"Tu es le cycliste {cyclist.id} (équipe {cyclist.team}).\n"
        f"Position: {cyclist.pos}/{state.track_length}. Énergie: {cyclist.energy}/5.\n"
        f"Potion utilisée: {'oui' if cyclist.potion_used else 'non'}.\n"
        f"Devant toi: {[c.id for c in ahead]}.\n"
        f"Derrière toi: {[c.id for c in behind]}.\n"
    )

    if energy_advice:
        prompt += f"\nConseil énergie (spécialiste équipe): {energy_advice}"
    if strategy_advice:
        prompt += f"\nConseil stratégie (stratège global): {strategy_advice}"

    prompt += (
        "\n\nActions disponibles:\n"
        "- advance: avance d'1 case (coûte 1 énergie)\n"
        "- slow: avance lentement (récupère +1 énergie si > 1)\n"
        "- draft: reste dans le sillage du cycliste devant (+1 énergie)\n"
        "- potion: boost unique +3 énergie puis advance\n"
        "- wait: reste sur place, récupère +1 énergie\n"
        "\nRéponds avec UN SEUL mot parmi: advance slow draft potion wait"
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
    Décide de l'action du cycliste en consultant les recommandations disponibles.

    POINT PÉDAGOGIQUE :
    - get_nowait() = lecture non-bloquante : si les spécialistes n'ont pas encore répondu,
      le cycliste décide quand même (avec moins d'info)
    - Illustre la tolérance aux pannes : un agent absent ne bloque pas les autres
    - reco_q est partagée entre les 3 cyclistes de l'équipe (Queue FIFO) :
      seul le premier cycliste à appeler get_nowait() obtient le conseil énergie.
      Comportement intentionnel — cf. spec section 9.
    """
    client = anthropic.Anthropic()

    while True:
        msg = input_q.get()   # bloquant — attend le prochain état
        if msg == STOP:
            break

        state = deserialize_state(msg)
        cyclist = next(c for c in state.cyclists if c.id == cyclist_id)

        # Consultation non-bloquante des spécialistes
        # Note : le tick du conseil n'est pas validé intentionnellement.
        # Un conseil d'un tick précédent est préférable à l'absence de conseil
        # (tolérance au décalage temporel entre spécialistes et cyclistes).
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
# ORCHESTRATEUR ET BOUCLE PRINCIPALE
# =============================================================================

def spawn_all(
    state: RaceState,
) -> tuple[dict[str, "AgentProcess"], dict[str, "SpecialistProcess"], multiprocessing.Queue, dict[str, multiprocessing.Queue]]:
    """
    Lance les 13 processus background.

    Retourne:
        agents       : cyclist_id → AgentProcess
        specialists  : nom → SpecialistProcess
        strategy_q   : queue partagée (écrite par stratège, lue par cyclistes)
        reco_queues  : team → Queue (écrite par energy worker, lue par cyclistes équipe)
    """
    strategy_q: multiprocessing.Queue = multiprocessing.Queue()
    reco_queues: dict[str, multiprocessing.Queue] = {}
    specialists: dict[str, SpecialistProcess] = {}

    # Stratège global (1 processus)
    sq_input: multiprocessing.Queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=strategist_worker, args=(sq_input, strategy_q), daemon=True)
    p.start()
    specialists["strategist"] = SpecialistProcess(p, sq_input)

    # Analystes énergie (3 processus, un par équipe)
    teams = list(dict.fromkeys(c.team for c in state.cyclists))
    for team in teams:
        rq: multiprocessing.Queue = multiprocessing.Queue()
        reco_queues[team] = rq
        eq_input: multiprocessing.Queue = multiprocessing.Queue()
        p = multiprocessing.Process(target=energy_worker, args=(team, eq_input, rq), daemon=True)
        p.start()
        specialists[f"energy_{team}"] = SpecialistProcess(p, eq_input)

    # Cyclistes (9 processus)
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
    Envoie l'état à tous les processus, collecte les actions avec deadline partagée.

    POINT PÉDAGOGIQUE :
    - Deadline partagée = les 9 agents ont 5s au total (pas 5s chacun).
      Le dernier agent à répondre peut avoir moins d'1s.
    - Si un cycliste dépasse la deadline : fallback "advance" (tolérance aux pannes).
    """
    msg = serialize_state(state)
    active = [c for c in state.cyclists if c.id not in state.finished]

    # Envoyer aux spécialistes (ils publient dans reco_queues / strategy_q)
    for spec in specialists.values():
        spec.input_q.put(msg)

    # Envoyer aux cyclistes actifs
    for c in active:
        agents[c.id].input_q.put(msg)

    # Collecter avec deadline partagée de 5 secondes
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
    Boucle principale — version background agents.

    POINT PÉDAGOGIQUE — Comparaison avec race.py :
    ┌─────────────────┬──────────────────────┬──────────────────────────┐
    │                 │ race.py (foreground)  │ race_bg.py (background)  │
    ├─────────────────┼──────────────────────┼──────────────────────────┤
    │ Parallélisme    │ Coopératif (asyncio)  │ Préemptif (OS scheduler) │
    │ Agents          │ Coroutines éphémères  │ Processus persistants    │
    │ Communication   │ Appel de fonction     │ multiprocessing.Queue    │
    │ Isolation       │ Mémoire partagée      │ Mémoire séparée          │
    │ Timeout         │ Non                   │ Oui (deadline 5s)        │
    │ Spécialisation  │ Non                   │ Oui (3 couches)          │
    └─────────────────┴──────────────────────┴──────────────────────────┘
    """
    state = init_race(60, ["A", "B", "C"], 3)
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    print(f"✓ 13 processus lancés (1 stratège + 3 energy workers + 9 cyclistes)")
    print(render(state))

    while not race_over(state):
        actions = orchestrator_bg(state, agents, specialists)
        state = resolve(state, actions)
        print(render(state))
        time.sleep(0.3)

    print(f"\n🏆 VAINQUEUR : Équipe {winner(state)} !")

    # Arrêt propre de tous les workers
    all_workers = list(agents.values()) + list(specialists.values())
    for w in all_workers:
        w.input_q.put(STOP)
    for w in all_workers:
        w.process.join(timeout=3)


if __name__ == "__main__":
    # spawn requis pour éviter les fork-safety issues avec le SDK Anthropic
    # (défaut sur macOS/Windows ; préférable sur Linux aussi)
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass  # Déjà configuré
    main()
