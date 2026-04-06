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
