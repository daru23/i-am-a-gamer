"""Phase 3: state rendering, notes, executor danger check and the agent loop.

Engine tests are skipped until scripts/build_engine.sh has been run.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from agent.bridge import Bridge, GameState, Status, default_binary
from agent.executor import Executor
from agent.loop import RunLog, play_level
from agent.notes import LevelNotes
from agent.providers.base import CallStats, Plan
from agent.state import render_state

BINARY = default_binary()
needs_engine = pytest.mark.skipif(not BINARY.exists(), reason="engine not built: run scripts/build_engine.sh")
SEED = 1234
FATAL = ["up", "down"]  # level 1: eats the base under an infotron, which then falls on Murphy


# --- state ---------------------------------------------------------------

def fake_state(rows: list[str], murphy: tuple[int, int], infotrons_left: int = 2) -> GameState:
    codes = {"#": 6, ":": 2, ".": 0, "I": 4, "O": 1, "E": 7, "M": 3, "S": 17, "c": 5}
    tiles = [[codes[c] for c in row] for row in rows]
    status = Status(level=1, tick=0, alive=True, won=False, over=False,
                    infotrons_left=infotrons_left, murphy=murphy, rng=0)
    return GameState(status=status, gravity=False, width=len(rows[0]), height=len(rows),
                     tiles=tiles, states=[[0] * len(rows[0]) for _ in rows])


ROWS = [
    "##########",
    "#OOOOOOOO#",   # decoration Murphy can't reach
    "##########",
    "#M::I...E#",
    "#.:O::I..#",
    "##########",
]


def test_render_crops_to_reachable_area():
    text = render_state(fake_state(ROWS, (1, 3)))
    map_part = text.split("Map (area Murphy can reach):\n")[1]
    assert "OOOOOOOO" not in map_part
    assert "  3 #M::I...E#" in map_part and "  2 ##########" in map_part


def test_render_lists_targets_and_status():
    text = render_state(fake_state(ROWS, (1, 3)))
    assert "Murphy at x=1, y=3. Infotrons still needed: 2." in text
    assert "Nearest infotrons: (4,3) dx=+3 dy=+0, (6,4) dx=+5 dy=+1" in text
    assert "Exit: (8,3) dx=+7 dy=+0" in text


def test_render_has_rulers():
    text = render_state(fake_state(ROWS, (1, 3)))
    map_part = text.split("Map (area Murphy can reach):\n")[1].splitlines()
    assert map_part[0].strip() == "0000000000" and map_part[1].strip() == "0123456789"


def test_neighbours_explain_each_move():
    rows = ["######",
            "#.O:I#",
            "#OM.O#",
            "#:O:E#",
            "######"]
    text = render_state(fake_state(rows, (2, 2)))
    assert "up    (2,1): zonk: blocked" in text
    assert "left  (1,2): zonk: blocked (no empty tile behind it)" in text
    assert "right (3,2): empty" in text
    rows[2] = "#.MO.#"
    text = render_state(fake_state(rows, (2, 2)))
    assert "right (3,2): zonk: can be pushed (empty tile behind it; give 'right' twice in a row)" in text


def test_neighbours_say_when_exit_opens():
    rows = ["#####", "#ME.#", "#####"]
    assert "exit: closed until 2 more" in render_state(fake_state(rows, (1, 1)))
    assert "exit: walk in to finish" in render_state(fake_state(rows, (1, 1), infotrons_left=0))


def test_render_says_go_to_exit_when_done():
    assert "walk onto the exit" in render_state(fake_state(ROWS, (1, 3), infotrons_left=0))


def test_unknown_tiles_render_as_moving():
    state = fake_state(ROWS, (1, 3))
    state.tiles[4][1] = 0xBB
    assert "  4 #~:O::I..#" in render_state(state)


# --- notes ---------------------------------------------------------------

def test_notes_keep_last_lessons(tmp_path):
    notes = LevelNotes(3, tmp_path)
    for i in range(12):
        notes.add(f"lesson {i}\nwith newline")
    lessons = notes.lessons()
    assert len(lessons) == 8 and lessons[-1] == "lesson 11 with newline"
    assert notes.path.name == "level_03.md"
    assert notes.text().startswith("- lesson 4")


# --- engine fixtures ---------------------------------------------------------

@pytest.fixture
def engines():
    tmp = Path(tempfile.mkdtemp(prefix="sp-", dir="/tmp"))  # short socket paths for macOS
    bridges = [Bridge.launch(BINARY, tmp / "main.sock", seed=SEED),
               Bridge.launch(BINARY, tmp / "shadow.sock", seed=SEED)]
    yield bridges
    for b in bridges:
        b.close()
    shutil.rmtree(tmp, ignore_errors=True)


# --- executor ------------------------------------------------------------

@needs_engine
def test_executor_without_shadow_dies(engines):
    main, _ = engines
    ex = Executor(main, None, level=1, seed=SEED)
    ex.start()
    result = ex.run(FATAL + ["wait"] * 3)
    assert result.outcome == "died" and not result.status.alive


@needs_engine
def test_executor_refuses_fatal_move(engines):
    main, shadow = engines
    ex = Executor(main, shadow, level=1, seed=SEED)
    ex.start()
    result = ex.run(FATAL + ["wait"])
    assert result.outcome == "aborted:danger"
    assert result.executed == ["up"] and result.status.alive
    assert "move 2 'down'" in result.detail
    # Murphy stays alive afterwards, and play continues from the same position.
    after = ex.run(["right"])
    assert after.outcome == "ok" and after.status.murphy == (31, 19)


@needs_engine
def test_executor_safe_plan_runs_fully(engines):
    main, shadow = engines
    ex = Executor(main, shadow, level=1, seed=SEED)
    ex.start()
    result = ex.run(["left", "left", "left", "left"])  # walks onto the infotron at (26,20)
    assert result.outcome == "ok" and result.executed == ["left"] * 4
    assert result.status.murphy == (26, 20) and result.infotrons_collected == 1


@needs_engine
def test_executor_reports_blocked_moves(engines):
    main, shadow = engines
    ex = Executor(main, shadow, level=1, seed=SEED)
    ex.start()
    ex.run(["right"] * 4)                 # to (34,20), next to an unpushable zonk
    result = ex.run(["right", "left"])
    assert "blocked): 1 'right'" in result.detail
    assert result.status.murphy == (33, 20)


@needs_engine
def test_shadow_stays_in_sync(engines):
    main, shadow = engines
    ex = Executor(main, shadow, level=1, seed=SEED)
    ex.start()
    for plan in (["left", "left"], FATAL, ["down", "right", "right"], ["up", "up"]):
        ex.run(plan)
    # The shadow is resynced after every plan: identical state, tick and RNG.
    assert shadow.get_state() == main.get_state()


# --- loop ----------------------------------------------------------------

class ScriptedProvider:
    """Plays fixed plans in order; reflection calls get a fixed lesson."""
    name, model = "scripted", "script"

    def __init__(self, plans: list[list[str]]):
        self.plans = plans
        self.i = 0
        self.prompts: list[tuple[str, str]] = []

    def decide(self, state, notes, history):
        self.prompts.append((state, notes))
        stats = CallStats(self.name, self.model, 1.0)
        if state.startswith("This attempt"):
            return Plan(reasoning="Do not walk back under a falling infotron.", moves=["wait"]), stats
        moves = self.plans[self.i % len(self.plans)]
        self.i += 1
        return Plan(reasoning="scripted", moves=moves), stats


CONFIG = {"engine": {"seed": SEED},
          "agent": {"max_attempts_per_level": 2, "max_decisions_per_attempt": 3, "ticks_per_move": 8}}


@needs_engine
def test_loop_without_shadow_counts_deaths_and_writes_notes(engines, tmp_path):
    main, _ = engines
    provider = ScriptedProvider([FATAL + ["wait"] * 3])
    log = RunLog(tmp_path, echo=None)
    notes = LevelNotes(1, tmp_path / "notes")
    result = play_level(1, provider, main, None, CONFIG, log, notes)
    assert not result.solved and result.attempts == 2 and result.deaths == 2
    assert notes.lessons() == ["Do not walk back under a falling infotron."] * 2
    # The second attempt's prompt carries the lesson from the first.
    assert "Do not walk back" in provider.prompts[2][1]
    events = [json.loads(l)["event"] for l in log.path.read_text().splitlines()]
    assert events.count("death") == 2 and events[-1] == "level_skipped"


@needs_engine
def test_loop_with_shadow_avoids_death(engines, tmp_path):
    main, shadow = engines
    provider = ScriptedProvider([FATAL])
    result = play_level(1, provider, main, shadow, CONFIG, RunLog(None, echo=None),
                        LevelNotes(1, tmp_path))
    assert result.deaths == 0 and not result.solved  # gives up after max decisions, alive


@needs_engine
def test_escaping_in_time_is_allowed(engines):
    # Same start, but stepping right gets Murphy out from under the infotron.
    main, shadow = engines
    ex = Executor(main, shadow, level=1, seed=SEED)
    ex.start()
    assert ex.run(FATAL + ["right"]).outcome == "ok"
