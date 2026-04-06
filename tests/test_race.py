import pytest
import asyncio
import json
import tempfile
import os
from unittest.mock import AsyncMock, patch, MagicMock
from race import (Cyclist, RaceState, Action, init_race, race_over, winner,
                  resolve, pos_to_xy, render, build_prompt, parse_action,
                  cyclist_agent, orchestrator, _VALID_ACTIONS, load_config,
                  validate_config, init_race_from_config, write_results)

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
    # All at positions 0..8 (staggered start to avoid overlap)
    positions = [c.pos for c in state.cyclists]
    assert len(set(positions)) == 9  # all different
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
    # speed = energy (default 5) → advances 5 cells; patch RNG to avoid flakiness
    state = _make_state(_c("A1","A",5))
    with patch("race.random.randint", return_value=0):
        new = resolve(state, {"A1": "advance"})
    assert new.cyclists[0].pos == 10

def test_resolve_wait_does_not_move():
    state = _make_state(_c("A1","A",5))
    new = resolve(state, {"A1": "wait"})
    assert new.cyclists[0].pos == 5

def test_resolve_superposition_allowed():
    # Two cyclists can share the same cell (overtaking allowed)
    # A1(pos=5,energy=5) advances → 10 ; B1(pos=5,energy=5) advances → 10
    state = _make_state(_c("A1","A",5), _c("B1","B",5))
    with patch("race.random.randint", return_value=0):
        new = resolve(state, {"A1": "advance", "B1": "advance"})
    positions = [c.pos for c in new.cyclists]
    assert positions[0] == positions[1] == 10  # both on the same cell

def test_resolve_energy_decreases_at_front():
    # leading, advancing: cost = energy//2 = 5//2 = 2 → energy 5-2 = 3
    state = _make_state(_c("A1","A",10, energy=5))
    new = resolve(state, {"A1": "advance"})
    assert new.cyclists[0].energy == 3

def test_resolve_energy_increases_drafting():
    # B1 exhausted (energy 1) follows A1 (energy 5): speed=1, cost 0, draft bonus +1
    # A1 pos=10→15 (speed 5), B1 pos=9→10 (speed 1); gap=5 ≤ 5 → drafting
    # B1 energy_delta = -(1//2) + 1 = 0 + 1 = +1 → energy 1→2; patch RNG for determinism
    state = _make_state(_c("A1","A",10), _c("B1","B",9, energy=1))
    with patch("race.random.randint", return_value=0):
        new = resolve(state, {"A1": "advance", "B1": "advance"})
    b1 = next(c for c in new.cyclists if c.id == "B1")
    assert b1.energy == 2  # exhausted cyclist recovers by following a fast leader

def test_resolve_energy_clamped_at_5():
    # A1 (energy 5) at pos=20 → pos 25. B1 (energy 5, draft) at pos=16 → pos 21.
    # Gap = 4 ≤ 5 → B1 drafting → energy_delta = +1 → clamped at 5.
    state = _make_state(_c("A1","A",20, energy=5), _c("B1","B",16, energy=5))
    with patch("race.random.randint", return_value=0):
        new = resolve(state, {"A1": "advance", "B1": "draft"})
    b1 = next(c for c in new.cyclists if c.id == "B1")
    assert b1.energy == 5  # clamped at 5, not exceeded

def test_resolve_energy_min_1():
    state = _make_state(_c("A1","A",10, energy=1))
    new = resolve(state, {"A1": "advance"})
    assert new.cyclists[0].energy == 1  # never below 1

def test_resolve_potion_adds_energy():
    # energy 2, potion, leading: cost=-(2//2)=-1, potion=+3 → delta=+2 → energy=4
    state = _make_state(_c("A1","A",10, energy=2, potion_used=False))
    new = resolve(state, {"A1": "potion"})
    a1 = new.cyclists[0]
    assert a1.potion_used is True
    assert a1.energy == 4  # 2 -1(cost) +3(potion) = 4

def test_resolve_potion_already_used_no_effect():
    # energy 3, potion already used, leading: cost=-(3//2)=-1 → energy=2
    state = _make_state(_c("A1","A",10, energy=3, potion_used=True))
    new = resolve(state, {"A1": "potion"})
    a1 = new.cyclists[0]
    assert a1.energy == 2  # potion ignored → speed cost only

def test_resolve_adds_to_finished():
    state = _make_state(_c("A1","A",19, energy=5))
    new = resolve(state, {"A1": "advance"})
    assert "A1" in new.finished

def test_resolve_tick_increments():
    state = _make_state(_c("A1","A",5))
    new = resolve(state, {"A1": "advance"})
    assert new.tick == 1

def test_pos_to_xy_segment_0_left_to_right():
    # Track of 30 cells, 3 segments of 10
    # Segment 0: pos 0-9 → row 0, col 0-9
    assert pos_to_xy(0, 30) == (0, 0)
    assert pos_to_xy(5, 30) == (0, 5)
    assert pos_to_xy(9, 30) == (0, 9)

def test_pos_to_xy_segment_1_right_to_left():
    # Segment 1: pos 10-19 → row 1, col 9-0 (reversed)
    assert pos_to_xy(10, 30) == (1, 9)
    assert pos_to_xy(15, 30) == (1, 4)
    assert pos_to_xy(19, 30) == (1, 0)

def test_pos_to_xy_segment_2_left_to_right():
    # Segment 2: pos 20-29 → row 2, col 0-9
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
    assert "used" in prompt.lower()

def test_parse_action_valid():
    assert parse_action("advance") == "advance"
    assert parse_action("ADVANCE") == "advance"
    assert parse_action("  draft  ") == "draft"

def test_parse_action_fallback_on_invalid():
    # If Claude replies with anything invalid → fallback "advance"
    assert parse_action("je veux aller vite") == "advance"
    assert parse_action("") == "advance"
    assert parse_action("go go go") == "advance"

def test_parse_action_extracts_word_from_sentence():
    # Claude may reply "I choose draft" → we extract "draft"
    assert parse_action("I choose draft") == "draft"
    assert parse_action("Ma décision: slow") == "slow"


def make_mock_response(text: str):
    """Creates a fake Anthropic response object."""
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

    # All cyclists have an action (fallback for the first one)
    assert len(actions) == 9
    assert all(a in _VALID_ACTIONS for a in actions.values())


# --- Helpers for config tests ---

def _write_config(config: dict) -> str:
    """Writes a config to a temp file, returns the path."""
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    json.dump(config, f)
    f.close()
    return f.name

VALID_CONFIG = {
    "track_length": 60,
    "teams": [
        {"name": "A", "riders": [{"id": "A1", "energy": 5}, {"id": "A2", "energy": 3}, {"id": "A3", "energy": 5}]},
        {"name": "B", "riders": [{"id": "B1", "energy": 4}, {"id": "B2", "energy": 5}, {"id": "B3", "energy": 2}]},
        {"name": "C", "riders": [{"id": "C1", "energy": 5}, {"id": "C2", "energy": 5}, {"id": "C3", "energy": 1}]},
    ]
}

# --- validate_config ---

def test_validate_config_valid():
    validate_config(VALID_CONFIG)  # must not raise

def test_validate_config_missing_track_length():
    bad = {k: v for k, v in VALID_CONFIG.items() if k != "track_length"}
    with pytest.raises(ValueError, match="track_length"):
        validate_config(bad)

def test_validate_config_track_length_out_of_range():
    bad = {**VALID_CONFIG, "track_length": 5}
    with pytest.raises(ValueError, match="track_length"):
        validate_config(bad)

def test_validate_config_wrong_team_count():
    bad = {**VALID_CONFIG, "teams": VALID_CONFIG["teams"][:2]}
    with pytest.raises(ValueError, match="3 teams"):
        validate_config(bad)

def test_validate_config_wrong_rider_count():
    bad_teams = [
        {"name": "A", "riders": [{"id": "A1", "energy": 5}, {"id": "A2", "energy": 5}]},  # 2 instead of 3
        VALID_CONFIG["teams"][1],
        VALID_CONFIG["teams"][2],
    ]
    bad = {**VALID_CONFIG, "teams": bad_teams}
    with pytest.raises(ValueError, match="3 cyclists"):
        validate_config(bad)

def test_validate_config_energy_out_of_range():
    bad_teams = [
        {"name": "A", "riders": [{"id": "A1", "energy": 6}, {"id": "A2", "energy": 5}, {"id": "A3", "energy": 5}]},
        VALID_CONFIG["teams"][1],
        VALID_CONFIG["teams"][2],
    ]
    bad = {**VALID_CONFIG, "teams": bad_teams}
    with pytest.raises(ValueError, match="energy"):
        validate_config(bad)

# --- load_config ---

def test_load_config_reads_file():
    path = _write_config(VALID_CONFIG)
    try:
        config = load_config(path)
        assert config["track_length"] == 60
        assert len(config["teams"]) == 3
    finally:
        os.unlink(path)

def test_load_config_raises_on_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.json")

def test_load_config_raises_on_invalid_config():
    bad = {**VALID_CONFIG, "track_length": 5}
    path = _write_config(bad)
    try:
        with pytest.raises(ValueError):
            load_config(path)
    finally:
        os.unlink(path)

# --- init_race_from_config ---

def test_init_race_from_config_uses_custom_energies():
    state = init_race_from_config(VALID_CONFIG)
    a2 = next(c for c in state.cyclists if c.id == "A2")
    b3 = next(c for c in state.cyclists if c.id == "B3")
    assert a2.energy == 3   # defined in VALID_CONFIG
    assert b3.energy == 2

def test_init_race_from_config_uses_track_length():
    state = init_race_from_config(VALID_CONFIG)
    assert state.track_length == 60

def test_init_race_from_config_correct_cyclist_count():
    state = init_race_from_config(VALID_CONFIG)
    assert len(state.cyclists) == 9

def test_init_race_from_config_positions_unique():
    state = init_race_from_config(VALID_CONFIG)
    positions = [c.pos for c in state.cyclists]
    assert len(set(positions)) == 9

def test_write_results_creates_file():
    state = init_race_from_config(VALID_CONFIG)
    decisions_log = [{"tick": 0, "decisions": {"A1": "advance", "B1": "draft"}}]
    path = tempfile.mktemp(suffix=".json")
    try:
        write_results(state, decisions_log, path)
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert "winner" in data
        assert "ticks" in data
        assert "finished_order" in data
        assert "config_summary" in data
        assert data["ticks"] == state.tick
    finally:
        if os.path.exists(path):
            os.unlink(path)

def test_write_results_config_summary_contains_energies():
    state = init_race_from_config(VALID_CONFIG)
    decisions_log = []
    path = tempfile.mktemp(suffix=".json")
    try:
        write_results(state, decisions_log, path)
        with open(path) as f:
            data = json.load(f)
        energies = data["config_summary"]["initial_energies"]
        assert energies["A2"] == 3   # VALID_CONFIG has A2 energy=3
        assert energies["B3"] == 2
    finally:
        if os.path.exists(path):
            os.unlink(path)
