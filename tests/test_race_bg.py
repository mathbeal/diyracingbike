import multiprocessing as mp
import pytest
from race import init_race, Cyclist, RaceState
from race_bg import serialize_state, deserialize_state, AgentProcess, SpecialistProcess, STOP, energy_worker, strategist_worker


def _make_state() -> RaceState:
    return init_race(60, ["A", "B", "C"], 3)


# --- serialize_state ---

def test_serialize_state_has_required_keys():
    data = serialize_state(_make_state())
    assert "tick" in data
    assert "track_length" in data
    assert "cyclists" in data
    assert "finished" in data


def test_serialize_state_cyclist_count():
    data = serialize_state(_make_state())
    assert len(data["cyclists"]) == 9


def test_serialize_state_cyclist_has_required_fields():
    data = serialize_state(_make_state())
    c = data["cyclists"][0]
    for field in ("id", "team", "pos", "energy", "potion_used"):
        assert field in c, f"champ manquant: {field}"


def test_serialize_state_track_length():
    data = serialize_state(_make_state())
    assert data["track_length"] == 60


def test_serialize_state_tick_zero():
    data = serialize_state(_make_state())
    assert data["tick"] == 0


# --- deserialize_state ---

def test_deserialize_state_roundtrip():
    state = _make_state()
    restored = deserialize_state(serialize_state(state))
    assert restored.track_length == state.track_length
    assert restored.tick == state.tick
    assert len(restored.cyclists) == len(state.cyclists)
    assert restored.finished == state.finished


def test_deserialize_state_preserves_cyclist_data():
    state = _make_state()
    restored = deserialize_state(serialize_state(state))
    for orig in state.cyclists:
        r = next(c for c in restored.cyclists if c.id == orig.id)
        assert r.pos == orig.pos
        assert r.energy == orig.energy
        assert r.team == orig.team
        assert r.potion_used == orig.potion_used


def test_deserialize_state_returns_race_state():
    restored = deserialize_state(serialize_state(_make_state()))
    assert isinstance(restored, RaceState)
    assert all(isinstance(c, Cyclist) for c in restored.cyclists)


def test_deserialize_state_roundtrip_with_finished():
    """Vérifie que la liste 'finished' est préservée correctement."""
    state = RaceState(
        track_length=60,
        cyclists=[
            Cyclist(id="A1", team="A", pos=55, energy=3, potion_used=False),
            Cyclist(id="B1", team="B", pos=60, energy=5, potion_used=False),
        ],
        tick=10,
        finished=["B1", "A1"],
    )
    restored = deserialize_state(serialize_state(state))
    assert restored.finished == ["B1", "A1"]
    assert len(restored.finished) == 2


@pytest.fixture(scope="session", autouse=True)
def _set_spawn():
    """Configure le start method une seule fois pour toute la session de tests."""
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass  # Déjà configuré


def test_energy_worker_starts_and_stops_cleanly():
    """energy_worker démarre et se termine sur signal STOP sans appel Claude."""
    input_q = mp.Queue()
    reco_q = mp.Queue()
    p = mp.Process(target=energy_worker, args=("A", input_q, reco_q), daemon=True)
    p.start()
    input_q.put(STOP)
    p.join(timeout=5)
    assert not p.is_alive(), "energy_worker ne s'est pas arrêté dans les 5s"


def test_strategist_worker_starts_and_stops_cleanly():
    """strategist_worker démarre et se termine sur signal STOP sans appel Claude."""
    input_q = mp.Queue()
    strategy_q = mp.Queue()
    p = mp.Process(target=strategist_worker, args=(input_q, strategy_q), daemon=True)
    p.start()
    input_q.put(STOP)
    p.join(timeout=5)
    assert not p.is_alive(), "strategist_worker ne s'est pas arrêté dans les 5s"


from race_bg import build_prompt_bg, cyclist_worker


def test_build_prompt_bg_contains_cyclist_id():
    state = _make_state()
    cyclist = next(c for c in state.cyclists if c.id == "A1")
    prompt = build_prompt_bg(cyclist, state, "", "")
    assert "A1" in prompt


def test_build_prompt_bg_contains_position():
    state = _make_state()
    cyclist = next(c for c in state.cyclists if c.id == "A1")
    prompt = build_prompt_bg(cyclist, state, "", "")
    assert str(cyclist.pos) in prompt


def test_build_prompt_bg_includes_energy_advice():
    state = _make_state()
    cyclist = next(c for c in state.cyclists if c.id == "A1")
    prompt = build_prompt_bg(cyclist, state, "Économise l'énergie", "")
    assert "Économise l'énergie" in prompt


def test_build_prompt_bg_includes_strategy_advice():
    state = _make_state()
    cyclist = next(c for c in state.cyclists if c.id == "A1")
    prompt = build_prompt_bg(cyclist, state, "", "Attaque maintenant")
    assert "Attaque maintenant" in prompt


def test_build_prompt_bg_no_advice_sections_when_empty():
    state = _make_state()
    cyclist = next(c for c in state.cyclists if c.id == "A1")
    prompt = build_prompt_bg(cyclist, state, "", "")
    assert "Conseil énergie" not in prompt
    assert "Conseil stratégie" not in prompt


def test_build_prompt_bg_lists_valid_actions():
    state = _make_state()
    cyclist = next(c for c in state.cyclists if c.id == "A1")
    prompt = build_prompt_bg(cyclist, state, "", "")
    for action in ("advance", "slow", "draft", "potion", "wait"):
        assert action in prompt


def test_cyclist_worker_starts_and_stops_cleanly():
    """cyclist_worker démarre et se termine sur signal STOP sans appel Claude."""
    input_q = mp.Queue()
    output_q = mp.Queue()
    reco_q = mp.Queue()
    strategy_q = mp.Queue()
    p = mp.Process(
        target=cyclist_worker,
        args=("A1", "A", input_q, output_q, reco_q, strategy_q),
        daemon=True,
    )
    p.start()
    input_q.put(STOP)
    p.join(timeout=5)
    assert not p.is_alive(), "cyclist_worker ne s'est pas arrêté dans les 5s"


from race_bg import spawn_all


def _cleanup(agents: dict, specialists: dict) -> None:
    """Arrête proprement tous les workers."""
    all_workers = list(agents.values()) + list(specialists.values())
    for w in all_workers:
        try:
            w.input_q.put(STOP)
        except Exception:
            pass
    for w in all_workers:
        w.process.join(timeout=3)


def test_spawn_all_creates_nine_cyclist_agents():
    """spawn_all crée exactement 9 processus cyclistes."""
    state = _make_state()
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    try:
        assert len(agents) == 9
    finally:
        _cleanup(agents, specialists)


def test_spawn_all_creates_four_specialists():
    """spawn_all crée 3 energy workers + 1 stratège = 4 spécialistes."""
    state = _make_state()
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    try:
        assert len(specialists) == 4
    finally:
        _cleanup(agents, specialists)


def test_spawn_all_creates_three_reco_queues():
    """Une reco_queue par équipe (A, B, C)."""
    state = _make_state()
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    try:
        assert set(reco_queues.keys()) == {"A", "B", "C"}
    finally:
        _cleanup(agents, specialists)


def test_spawn_all_all_processes_are_alive():
    """Tous les 13 processus tournent effectivement."""
    state = _make_state()
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    try:
        for ap in agents.values():
            assert ap.process.is_alive(), f"Processus {ap.cyclist_id} mort au démarrage"
        for name, sp in specialists.items():
            assert sp.process.is_alive(), f"Spécialiste {name} mort au démarrage"
    finally:
        _cleanup(agents, specialists)


def test_spawn_all_all_processes_are_daemon():
    """Tous les processus doivent être daemon=True pour éviter les zombies."""
    state = _make_state()
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    try:
        for ap in agents.values():
            assert ap.process.daemon is True, f"{ap.cyclist_id} n'est pas daemon"
        for name, sp in specialists.items():
            assert sp.process.daemon is True, f"{name} n'est pas daemon"
    finally:
        _cleanup(agents, specialists)


from race_bg import orchestrator_bg, AgentProcess, SpecialistProcess


def _mock_cyclist(cyclist_id: str, input_q, output_q):
    """Mock worker: lit l'état et répond immédiatement 'advance' sans appel Claude."""
    while True:
        msg = input_q.get()
        if msg == STOP:
            break
        output_q.put({"cyclist_id": cyclist_id, "action": "advance"})


def _mock_specialist(input_q):
    """Mock spécialiste: reçoit l'état et ne publie rien."""
    while True:
        msg = input_q.get()
        if msg == STOP:
            break


def test_orchestrator_bg_collects_all_actions():
    """orchestrator_bg collecte les actions de tous les cyclistes actifs."""
    state = _make_state()

    # Créer des agents mock (sans Claude)
    agents = {}
    for c in state.cyclists:
        iq = mp.Queue()
        oq = mp.Queue()
        p = mp.Process(target=_mock_cyclist, args=(c.id, iq, oq), daemon=True)
        p.start()
        agents[c.id] = AgentProcess(p, iq, oq, c.id)

    # Créer un spécialiste mock (reçoit l'état, ne publie rien)
    sq_input = mp.Queue()
    sp = mp.Process(target=_mock_specialist, args=(sq_input,), daemon=True)
    sp.start()
    specialists = {"strategist": SpecialistProcess(sp, sq_input)}

    try:
        actions = orchestrator_bg(state, agents, specialists)
        # Tous les 9 cyclistes doivent avoir répondu
        assert len(actions) == 9
        # Les mock workers répondent toujours "advance"
        assert all(action == "advance" for action in actions.values())
        # Les clés doivent être les IDs des cyclistes
        assert set(actions.keys()) == {c.id for c in state.cyclists}
    finally:
        _cleanup(agents, specialists)
