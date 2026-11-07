"""Engine bridge determinism: same seed + same inputs must give identical runs.

Needs the patched engine: scripts/build_engine.sh. Skipped if it isn't built.
"""
from __future__ import annotations

import random
import shutil
import tempfile
from pathlib import Path

import pytest

from agent.bridge import MOVE_INPUTS, Bridge, BridgeError, default_binary

BINARY = default_binary()
pytestmark = pytest.mark.skipif(not BINARY.exists(), reason="engine not built: run scripts/build_engine.sh")

SEED = 1234
TICKS_PER_MOVE = 8
MOVES = [m for m in MOVE_INPUTS if m != "space"]


@pytest.fixture
def sock_dir():
    # Unix socket paths are capped at 104 bytes on macOS; pytest's tmp_path can exceed that.
    path = Path(tempfile.mkdtemp(prefix="sp-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def launch(sock_dir):
    bridges: list[Bridge] = []

    def _launch(name: str = "a", seed: int = SEED) -> Bridge:
        bridge = Bridge.launch(BINARY, sock_dir / f"{name}.sock", seed=seed)
        bridges.append(bridge)
        return bridge

    yield _launch
    for bridge in bridges:
        bridge.close()


def scripted_moves(n: int, script_seed: int = 7) -> list[str]:
    rng = random.Random(script_seed)
    return [rng.choice(MOVES) for _ in range(n)]


def play(bridge: Bridge, level: int, moves: list[str], seed: int | None = None) -> list:
    """Run the moves and return a full snapshot after each one."""
    trace = [bridge.load_level(level, seed=seed)]
    for move in moves:
        status = bridge.step(move, TICKS_PER_MOVE)
        state = bridge.get_state()
        trace.append((status, state.tiles, state.states))
        if status.over:
            break
    return trace


@pytest.mark.parametrize("level", [1, 2, 4])
def test_two_processes_same_seed_and_inputs_match(launch, level):
    moves = scripted_moves(250)
    trace_a = play(launch("a"), level, moves)
    trace_b = play(launch("b"), level, moves)
    assert len(trace_a) > 10
    assert trace_a == trace_b


# (level, move-script seed) pairs whose scripted runs die and use the RNG
DEATH_RUNS = [(1, 0), (6, 0), (10, 1)]


@pytest.mark.parametrize("level,script_seed", DEATH_RUNS)
def test_runs_with_death_match_and_report_death(launch, level, script_seed):
    moves = scripted_moves(400, script_seed)
    trace_a = play(launch("a"), level, moves)
    trace_b = play(launch("b"), level, moves)
    assert trace_a == trace_b
    statuses = [t[0] for t in trace_a[1:]]
    assert len({s.rng for s in statuses}) > 1, "run should exercise the RNG"
    final = statuses[-1]
    assert final.over and not final.alive and not final.won


def test_steps_after_level_over_run_no_ticks(launch):
    bridge = launch()
    trace = play(bridge, 6, scripted_moves(400, 0))
    assert trace[-1][0].over
    status = bridge.step("wait", 10)
    assert status.ticks_run == 0 and status.over and not status.alive


def test_reloading_in_one_process_matches(launch):
    bridge = launch()
    moves = scripted_moves(250, script_seed=11)
    first = play(bridge, 1, moves)
    play(bridge, 3, scripted_moves(50))  # disturb engine state with another level
    second = play(bridge, 1, moves)
    assert first == second


def test_seed_changes_rng(launch):
    a, b = launch("a", seed=1), launch("b", seed=2)
    a.load_level(1)
    b.load_level(1)
    assert a.step("wait", 50).rng != b.step("wait", 50).rng


def test_load_level_seed_override(launch):
    bridge = launch(seed=1)
    assert bridge.load_level(1).rng == 1
    assert bridge.load_level(1, seed=999).rng == 999


def test_level_1_initial_state(launch):
    bridge = launch()
    status = bridge.load_level(1)
    assert (status.tick, status.alive, status.won, status.over) == (0, True, False, False)
    state = bridge.get_state()
    assert (state.width, state.height) == (60, 24)
    x, y = state.status.murphy
    assert state.tiles[y][x] == 3  # LevelTileTypeMurphy
    assert state.status.infotrons_left > 0


def test_walking_moves_one_tile_per_8_ticks(launch):
    bridge = launch()
    start = bridge.load_level(1).murphy
    status = bridge.step("right", 16)
    assert status.ticks_run == 16
    assert status.murphy == (start[0] + 2, start[1])


def test_errors(launch):
    bridge = launch()
    with pytest.raises(BridgeError, match="no level loaded"):
        bridge.step("up", 1)
    bridge.load_level(1)
    with pytest.raises(BridgeError, match="input"):
        bridge.step(42, 1)
    with pytest.raises(BridgeError, match="level"):
        bridge.load_level(0)
    bridge.step("wait", 1)  # connection still usable after errors
