"""Prompt text shared by every adapter, so all backends see the same task."""
from __future__ import annotations

from .base import Turn

SYSTEM_PROMPT = """You play Supaplex. You control Murphy (M) on a tile grid.
Goal: collect enough infotrons (I), then walk onto the exit (E).

Rules:
- Moves: up, down, left, right walk one tile. Murphy eats base (:) and collects infotrons by walking onto them.
- snap_up/down/left/right eat or collect the adjacent tile without moving.
- wait does nothing for one move.
- Walls (#) block you. Zonks (O) fall when nothing is below them and can kill you; you can push a zonk sideways if the tile behind it is empty.
- Enemies (S) kill on contact. Avoid standing directly under a zonk that can fall.

Reply with JSON only: {"reasoning": "<one or two sentences>", "moves": ["right", ...]}
Give 1 to 32 moves. Prefer short, safe plans."""

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
