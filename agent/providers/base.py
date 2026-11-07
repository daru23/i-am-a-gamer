"""Provider protocol and Plan schema shared by every model backend.

Adapters (ollama.py, claude.py, openai_compat.py) implement `Provider`.
Game logic never lives in an adapter: they only turn (state, notes, history)
into a validated Plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

Move = Literal[
    "up", "down", "left", "right",          # walk / eat base / collect
    "snap_up", "snap_down", "snap_left", "snap_right",  # space+dir: act without moving
    "wait",
]


class Plan(BaseModel):
    reasoning: str = Field(description="Short explanation of the plan")
    moves: list[Move] = Field(min_length=1, max_length=32)


WAIT_PLAN = Plan(reasoning="fallback: invalid model output", moves=["wait"])

PLAN_JSON_SCHEMA = Plan.model_json_schema()


@dataclass
class Turn:
    state: str
    plan: Plan
    outcome: str  # e.g. "ok", "aborted:danger", "died", "level_complete"


@dataclass
class CallStats:
    provider: str
    model: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    valid: bool = True
    extra: dict = field(default_factory=dict)


class Provider(Protocol):
    name: str
    model: str

    def decide(self, state: str, notes: str, history: list[Turn]) -> tuple[Plan, CallStats]:
        """Return a validated Plan. Implementations retry once on invalid
        output, then return WAIT_PLAN with stats.valid = False."""
        ...


def parse_plan(raw: str) -> Plan | None:
    try:
        return Plan.model_validate_json(raw)
    except ValidationError:
        return None
