"""The agent loop: read state -> ask the provider for a plan -> execute it with
the danger check -> log. Retries a level up to agent.max_attempts_per_level
times, then skips it."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .bridge import Bridge
from .executor import Executor
from .notes import LevelNotes
from .providers.base import Provider, Turn
from .state import render_state


@dataclass
class LevelResult:
    level: int
    solved: bool
    attempts: int
    deaths: int
    decisions: int
    ticks: int
    moves: list[str]  # inputs of the winning attempt (or the last one)


class RunLog:
    """JSON lines of everything that happens in a run: logs/run_<time>.jsonl."""

    def __init__(self, log_dir: str | Path | None, echo=lambda msg: print(msg, flush=True)):
        self.echo = echo
        self.path = None
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            self.path = Path(log_dir) / time.strftime("run_%Y%m%d-%H%M%S.jsonl")

    def event(self, kind: str, message: str | None = None, **fields) -> None:
        if message and self.echo:
            self.echo(message)
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps({"ts": time.time(), "event": kind, **fields}) + "\n")


REFLECT_PROMPT = """This attempt at level {level} failed: {how}.
Moves of the last turns:
{recent}

Final state:
{state}

Write ONE short lesson (one sentence) for your next attempt in "reasoning".
Set "moves" to ["wait"]."""


def play_level(level: int, provider: Provider, main: Bridge, shadow: Bridge | None,
               config: dict, log: RunLog, notes: LevelNotes | None = None) -> LevelResult:
    agent_cfg = config.get("agent", {})
    max_attempts = agent_cfg.get("max_attempts_per_level", 5)
    max_decisions = agent_cfg.get("max_decisions_per_attempt", 200)
    radius = agent_cfg.get("window_radius", 6)
    executor = Executor(
        main=main, shadow=shadow, level=level,
        seed=config.get("engine", {}).get("seed"),
        ticks_per_move=agent_cfg.get("ticks_per_move", 8),
        lookahead_moves=agent_cfg.get("lookahead_moves", 3),
    )
    notes = notes or LevelNotes(level)
    deaths = decisions = 0

    for attempt in range(1, max_attempts + 1):
        status = executor.start()
        history: list[Turn] = []
        how = f"no exit after {max_decisions} decisions"
        log.event("attempt_start", f"level {level} attempt {attempt}/{max_attempts}: "
                  f"{status.infotrons_left} infotrons needed",
                  level=level, attempt=attempt, infotrons_left=status.infotrons_left)

        for decision in range(1, max_decisions + 1):
            decisions += 1
            text = render_state(main.get_state(), radius)
            plan, stats = provider.decide(text, notes.text(), history)
            result = executor.run(list(plan.moves))
            history.append(Turn(state=text, plan=plan, outcome=result.summary()))
            log.event(
                "turn",
                f"  [{attempt}.{decision}] {stats.provider} {stats.latency_ms:.0f}ms "
                f"{' '.join(plan.moves)} -> {result.summary()}",
                level=level, attempt=attempt, decision=decision,
                provider=stats.provider, model=stats.model, latency_ms=round(stats.latency_ms),
                valid=stats.valid, reasoning=plan.reasoning, moves=list(plan.moves),
                executed=result.executed, outcome=result.outcome, detail=result.detail,
                murphy=list(result.status.murphy), infotrons_left=result.status.infotrons_left,
                tick=result.status.tick,
            )
            if result.outcome == "level_complete":
                log.event("level_complete", f"level {level} solved on attempt {attempt} "
                          f"({decisions} decisions, tick {result.status.tick})",
                          level=level, attempt=attempt, tick=result.status.tick,
                          moves=executor.committed)
                return LevelResult(level, True, attempt, deaths, decisions,
                                   result.status.tick, list(executor.committed))
            if result.outcome == "died":
                deaths += 1
                how = f"Murphy died at {result.status.murphy}"
                log.event("death", f"  died at {result.status.murphy}", level=level,
                          attempt=attempt, murphy=list(result.status.murphy),
                          tick=result.status.tick)
                break

        log.event("attempt_failed", f"level {level} attempt {attempt} failed: {how}",
                  level=level, attempt=attempt, reason=how)
        lesson = reflect(provider, level, how, history, render_state(main.get_state(), radius))
        if lesson:
            notes.add(lesson)
            log.event("note", f"  note: {lesson}", level=level, attempt=attempt, note=lesson)

    log.event("level_skipped", f"level {level} skipped after {max_attempts} attempts",
              level=level, attempts=max_attempts)
    return LevelResult(level, False, max_attempts, deaths, decisions,
                       executor.status.tick, list(executor.committed))


def reflect(provider: Provider, level: int, how: str, history: list[Turn], state: str) -> str:
    """Ask the model for a one-line lesson, through the normal plan schema."""
    recent = "\n".join(f"- {' '.join(t.plan.moves)} -> {t.outcome}" for t in history[-5:]) or "- none"
    prompt = REFLECT_PROMPT.format(level=level, how=how, recent=recent, state=state)
    plan, stats = provider.decide(prompt, notes="", history=[])
    return plan.reasoning.strip() if stats.valid else ""
