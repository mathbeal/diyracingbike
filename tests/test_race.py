import pytest
from race import Cyclist, RaceState, Action, init_race, race_over, winner, resolve

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
    assert a1.energy == 4  # 2 -1(front) +3(potion) = 4

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
