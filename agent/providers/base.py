"""Provider protocol and Plan schema shared by every model backend.

Adapters (ollama.py, claude.py, openai_compat.py) implement `Provider`.
Game logic never lives in an adapter: they only turn (state, notes, history)
into a validated Plan.
"""
from __future__ import annotations

import time
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


class ProviderError(RuntimeError):
    """The backend could not be reached or failed (timeout, HTTP error, auth).
    Distinct from invalid output, which is retried and then becomes WAIT_PLAN."""


@dataclass
class RawReply:
    """What one backend request returned: the plan as a JSON string or dict."""
    content: str | dict
    input_tokens: int | None = None
    output_tokens: int | None = None


class AdapterBase:
    """Shared decide() for adapters: build the prompt, call the backend,
    validate, retry once with the error appended, else return WAIT_PLAN.

    Subclasses implement `_request(system, user) -> RawReply` and raise
    ProviderError on transport failures."""

    name: str = ""
    model: str = ""

    def _request(self, system: str, user: str) -> RawReply:
        raise NotImplementedError

    def decide(self, state: str, notes: str, history: list[Turn]) -> tuple[Plan, CallStats]:
        from .prompt import SYSTEM_PROMPT, build_user_message, retry_suffix

        user = build_user_message(state, notes, history)
        stats = CallStats(provider=self.name, model=self.model, latency_ms=0.0)
        error = ""
        for attempt in (1, 2):
            prompt = user if attempt == 1 else user + retry_suffix(error)
            start = time.perf_counter()
            reply = self._request(SYSTEM_PROMPT, prompt)
            stats.latency_ms += (time.perf_counter() - start) * 1000
            stats.input_tokens = _add(stats.input_tokens, reply.input_tokens)
            stats.output_tokens = _add(stats.output_tokens, reply.output_tokens)
            stats.extra["attempts"] = attempt
            plan, error = _validate(reply.content)
            if plan is not None:
                return plan, stats
        stats.valid = False
        stats.extra["error"] = error
        return WAIT_PLAN, stats


def _validate(content: str | dict) -> tuple[Plan | None, str]:
    try:
        if isinstance(content, dict):
            return Plan.model_validate(content), ""
        return Plan.model_validate_json(content), ""
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "reply"
        return None, f"{loc}: {first['msg']}"


def _add(a: int | None, b: int | None) -> int | None:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)
