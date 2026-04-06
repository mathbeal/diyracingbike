import pytest
from race import Cyclist, RaceState, Action, init_race, race_over, winner, resolve, pos_to_xy, render, build_prompt, parse_action

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
