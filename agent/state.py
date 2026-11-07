"""Turn engine state into the text the model sees: legend, status, the reachable
part of the map with coordinate rulers, a local window around Murphy, and the
nearest infotrons. The model never sees screenshots.

Coordinates: x grows to the right, y grows downward. "up" is y-1.
"""
from __future__ import annotations

from collections import deque

from .bridge import GameState

# LevelTileType (engine/src/globals.h) -> one character
WALL = "#"
TILE_CHARS: dict[int, str] = {
    0: ".",    # space
    1: "O",    # zonk
    2: ":",    # base
    3: "M",    # murphy
    4: "I",    # infotron
    7: "E",    # exit
    8: "o",    # orange disk
    9: ">", 10: "v", 11: "<", 12: "^",   # one-way ports
    13: ">", 14: "v", 15: "<", 16: "^",  # special ports
    17: "S",   # snik snak
    18: "Y",   # yellow disk
    19: "T",   # terminal
    20: "R",   # red disk
    21: "|", 22: "-", 23: "+",           # two- and four-way ports
    24: "e",   # electron
    25: "B",   # bug
    31: "*",   # explosion
}
for _t in (5, 6, 26, 27, 28, 29, 30, 32, 33, 34, 35, 36, 37, 38, 39):
    TILE_CHARS[_t] = WALL  # hardware and RAM chips
MOVING = "~"  # transient engine markers (an object between two tiles)

LEGEND = (
    "Legend: M Murphy (you), # wall, : base (eat by walking), . empty, I infotron (collect),\n"
    "        E exit, O zonk (falls, pushable sideways), S snik snak / e electron (enemies, deadly),\n"
    "        o orange disk / Y yellow disk (explosive), R red disk, B bug (deadly when sparking),\n"
    "        T terminal, > v < ^ | - + ports, * explosion, ~ object in motion"
)

INFOTRON_LIST_MAX = 10


def tile_char(tile: int) -> str:
    return TILE_CHARS.get(tile, MOVING)


def char_grid(state: GameState) -> list[list[str]]:
    grid = [[tile_char(t) for t in row] for row in state.tiles]
    mx, my = state.status.murphy
    if 0 <= my < state.height and 0 <= mx < state.width:
        grid[my][mx] = "M"
    return grid


def reachable_box(grid: list[list[str]], start: tuple[int, int]) -> tuple[int, int, int, int]:
    """Bounding box (x0, y0, x1, y1 inclusive) of the region connected to Murphy
    through anything but walls, plus a one-tile margin. Hides decoration that
    Murphy can never reach (level 1's title art, for example)."""
    h, w = len(grid), len(grid[0])
    sx, sy = start
    seen = {(sx, sy)}
    queue = deque([(sx, sy)])
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and grid[ny][nx] != WALL:
                seen.add((nx, ny))
                queue.append((nx, ny))
    xs = [x for x, _ in seen]
    ys = [y for _, y in seen]
    return (max(min(xs) - 1, 0), max(min(ys) - 1, 0),
            min(max(xs) + 1, w - 1), min(max(ys) + 1, h - 1))


def render_region(grid: list[list[str]], x0: int, y0: int, x1: int, y1: int) -> str:
    """Rows labelled with y, two ruler lines on top for x (tens, then ones)."""
    pad = " " * 4
    xs = range(x0, x1 + 1)
    lines = [
        pad + "".join(str(x // 10 % 10) for x in xs),
        pad + "".join(str(x % 10) for x in xs),
    ]
    for y in range(y0, y1 + 1):
        lines.append(f"{y:>3} " + "".join(grid[y][x] for x in xs))
    return "\n".join(lines)


DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
TILE_NAMES = {
    ".": "empty", ":": "base (walk in to eat it)", "I": "infotron (walk in to collect)",
    "S": "snik snak enemy (deadly)", "e": "electron enemy (deadly)", "B": "bug (deadly when sparking)",
    "o": "orange disk", "Y": "yellow disk", "R": "red disk (collect)", "T": "terminal",
    "*": "explosion (deadly)", MOVING: "something moving (careful)",
    ">": "port", "v": "port", "<": "port", "^": "port", "|": "port", "-": "port", "+": "port",
}


def describe_neighbours(grid: list[list[str]], murphy: tuple[int, int], infotrons_left: int) -> str:
    """What each move would run into, so the model need not count columns."""
    mx, my = murphy
    h, w = len(grid), len(grid[0])
    lines = []
    for move, (dx, dy) in DIRECTIONS.items():
        x, y = mx + dx, my + dy
        c = grid[y][x] if 0 <= x < w and 0 <= y < h else WALL
        if c == WALL:
            what = "wall: blocked"
        elif c == "O":
            bx, by = x + dx, y + dy
            behind = grid[by][bx] if dy == 0 and 0 <= bx < w else None
            if behind == ".":
                what = f"zonk: can be pushed (empty tile behind it; give '{move}' twice in a row)"
            else:
                what = "zonk: blocked" + (" (no empty tile behind it)" if dy == 0 else "")
        elif c == "E":
            what = "exit: walk in to finish" if infotrons_left == 0 else \
                f"exit: closed until {infotrons_left} more infotrons are collected"
        else:
            what = TILE_NAMES.get(c, c)
        lines.append(f"  {move:<5} ({x},{y}): {what}")
    return "Next to Murphy:\n" + "\n".join(lines)


def render_state(state: GameState, window_radius: int = 6) -> str:
    grid = char_grid(state)
    s = state.status
    mx, my = s.murphy
    x0, y0, x1, y1 = reachable_box(grid, (mx, my))

    targets = sorted(
        ((abs(x - mx) + abs(y - my), x, y)
         for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if grid[y][x] == "I"),
    )
    exits = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if grid[y][x] == "E"]

    parts = [
        LEGEND,
        f"Murphy at x={mx}, y={my}. Infotrons still needed: {s.infotrons_left}. "
        f"Gravity: {'on' if state.gravity else 'off'}. Tick: {s.tick}.",
    ]
    if s.infotrons_left == 0:
        parts.append("All infotrons collected: walk onto the exit E to finish.")
    parts.append(describe_neighbours(grid, (mx, my), s.infotrons_left))
    if targets:
        listed = ", ".join(f"({x},{y}) dx={x - mx:+d} dy={y - my:+d}"
                           for _, x, y in targets[:INFOTRON_LIST_MAX])
        more = f" (+{len(targets) - INFOTRON_LIST_MAX} more)" if len(targets) > INFOTRON_LIST_MAX else ""
        parts.append(f"Nearest infotrons: {listed}{more}")
    if exits:
        parts.append("Exit: " + ", ".join(f"({x},{y}) dx={x - mx:+d} dy={y - my:+d}" for x, y in exits))

    wx0, wy0 = max(mx - window_radius, x0), max(my - window_radius, y0)
    wx1, wy1 = min(mx + window_radius, x1), min(my + window_radius, y1)
    parts.append(f"Close view around Murphy:\n{render_region(grid, wx0, wy0, wx1, wy1)}")
    parts.append(f"Map (area Murphy can reach):\n{render_region(grid, x0, y0, x1, y1)}")
    return "\n\n".join(parts)
