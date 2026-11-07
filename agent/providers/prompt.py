"""Prompt text shared by every adapter, so all backends see the same task."""
from __future__ import annotations

from .base import Turn

SYSTEM_PROMPT = """You play Supaplex. You control Murphy (M) on a tile grid.
Goal: collect enough infotrons (I), then walk onto the exit (E).

Coordinates: x grows to the right, y grows downward. "up" is y-1, "down" is y+1,
"left" is x-1, "right" is x+1. Each move goes exactly one tile. The map has rulers:
row numbers on the left, column numbers (tens over ones) on top.

Rules:
- Moves: up, down, left, right walk one tile. Murphy eats base (:) and collects infotrons by walking onto them.
- snap_up/down/left/right eat or collect the adjacent tile without moving.
- wait does nothing for one move.
- Walls (#) block you. Zonks (O) and infotrons (I) fall into empty space below them; a falling one kills Murphy.
  Eating the base under a zonk lets it fall, so step out of its column afterwards.
- You can push a zonk sideways if the tile behind it is empty (.): repeat the move twice, e.g. "left", "left".
- "Next to Murphy" says what each move runs into. If a direction is blocked, go around instead of repeating it.
- Enemies (S, e) kill on contact.
- Moves that would get Murphy killed are refused and you are told which move; plan around it.

Reply with JSON only: {"reasoning": "<one or two sentences>", "moves": ["right", ...]}
Give 1 to 32 moves. Head for the nearest infotron with a short plan, then plan again."""

HISTORY_TURNS = 5


def build_user_message(state: str, notes: str, history: list[Turn]) -> str:
    parts = []
    if notes.strip():
        parts.append(f"Notes from earlier attempts:\n{notes.strip()}")
    if history:
        lines = [
            f"- moves {' '.join(t.plan.moves)} -> {t.outcome}"
            for t in history[-HISTORY_TURNS:]
        ]
        parts.append("Recent turns:\n" + "\n".join(lines))
    parts.append(f"Current state:\n{state.rstrip()}")
    parts.append("Return your plan as JSON.")
    return "\n\n".join(parts)


def retry_suffix(error: str) -> str:
    return (
        f"\n\nYour previous reply was invalid ({error}). "
        'Reply again with only JSON matching {"reasoning": str, "moves": [str]}.'
    )
