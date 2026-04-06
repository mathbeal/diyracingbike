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
        assert field in c, f"missing field: {field}"


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
    """Verifies that the 'finished' list is preserved correctly."""
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
    """Configures the start method once for the entire test session."""
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass  # Already configured


def test_energy_worker_starts_and_stops_cleanly():
    """energy_worker starts and stops on STOP signal without calling Claude."""
    input_q = mp.Queue()
    reco_q = mp.Queue()
    p = mp.Process(target=energy_worker, args=("A", input_q, reco_q), daemon=True)
    p.start()
    input_q.put(STOP)
    p.join(timeout=5)
    assert not p.is_alive(), "energy_worker did not stop within 5s"


def test_strategist_worker_starts_and_stops_cleanly():
    """strategist_worker starts and stops on STOP signal without calling Claude."""
    input_q = mp.Queue()
    strategy_q = mp.Queue()
    p = mp.Process(target=strategist_worker, args=(input_q, strategy_q), daemon=True)
    p.start()
    input_q.put(STOP)
    p.join(timeout=5)
    assert not p.is_alive(), "strategist_worker did not stop within 5s"


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
    assert "Energy advice" not in prompt
    assert "Strategy advice" not in prompt


def test_build_prompt_bg_lists_valid_actions():
    state = _make_state()
    cyclist = next(c for c in state.cyclists if c.id == "A1")
    prompt = build_prompt_bg(cyclist, state, "", "")
    for action in ("advance", "slow", "draft", "potion", "wait"):
        assert action in prompt


def test_cyclist_worker_starts_and_stops_cleanly():
    """cyclist_worker starts and stops on STOP signal without calling Claude."""
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
    """Cleanly shuts down all workers."""
    all_workers = list(agents.values()) + list(specialists.values())
    for w in all_workers:
        try:
            w.input_q.put(STOP)
        except Exception:
            pass
    for w in all_workers:
        w.process.join(timeout=3)


def test_spawn_all_creates_nine_cyclist_agents():
    """spawn_all creates exactly 9 cyclist processes."""
    state = _make_state()
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    try:
        assert len(agents) == 9
    finally:
        _cleanup(agents, specialists)


def test_spawn_all_creates_four_specialists():
    """spawn_all creates 3 energy workers + 1 strategist = 4 specialists."""
    state = _make_state()
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    try:
        assert len(specialists) == 4
    finally:
        _cleanup(agents, specialists)


def test_spawn_all_creates_three_reco_queues():
    """One reco_queue per team (A, B, C)."""
    state = _make_state()
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    try:
        assert set(reco_queues.keys()) == {"A", "B", "C"}
    finally:
        _cleanup(agents, specialists)


def test_spawn_all_all_processes_are_alive():
    """All 13 processes are actually running."""
    state = _make_state()
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    try:
        for ap in agents.values():
            assert ap.process.is_alive(), f"Process {ap.cyclist_id} dead at startup"
        for name, sp in specialists.items():
            assert sp.process.is_alive(), f"Specialist {name} dead at startup"
    finally:
        _cleanup(agents, specialists)


def test_spawn_all_all_processes_are_daemon():
    """All processes must be daemon=True to avoid zombies."""
    state = _make_state()
    agents, specialists, strategy_q, reco_queues = spawn_all(state)
    try:
        for ap in agents.values():
            assert ap.process.daemon is True, f"{ap.cyclist_id} is not daemon"
        for name, sp in specialists.items():
            assert sp.process.daemon is True, f"{name} is not daemon"
    finally:
        _cleanup(agents, specialists)


from race_bg import orchestrator_bg, AgentProcess, SpecialistProcess


def _mock_cyclist(cyclist_id: str, input_q, output_q):
    """Mock worker: reads state and immediately replies 'advance' without calling Claude."""
    while True:
        msg = input_q.get()
        if msg == STOP:
            break
        output_q.put({"cyclist_id": cyclist_id, "action": "advance"})


def _mock_specialist(input_q):
    """Mock specialist: receives state and publishes nothing."""
    while True:
        msg = input_q.get()
        if msg == STOP:
            break


def test_orchestrator_bg_collects_all_actions():
    """orchestrator_bg collects actions from all active cyclists."""
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
        # All 9 cyclists must have responded
        assert len(actions) == 9
        # Mock workers always reply "advance"
        assert all(action == "advance" for action in actions.values())
        # Keys must be cyclist IDs
        assert set(actions.keys()) == {c.id for c in state.cyclists}
    finally:
        _cleanup(agents, specialists)
