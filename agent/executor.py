"""Run a plan move by move on the engine, aborting it before Murphy gets killed.

Danger check: a second headless "shadow" engine holds the same level and the
same committed inputs. Before a plan touches the real engine, the shadow runs
it (plus a few wait moves) to see whether Murphy survives. Because runs are
deterministic, the shadow shows exactly what the real engine would do. Only
the longest safe prefix of the plan is executed, and the outcome says which
move would have been fatal. The shadow is then reloaded and replayed to the
real engine's position (cheap: headless ticks run at ~200k/s).

The executor never picks moves; it only refuses the ones that kill Murphy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby

from .bridge import Bridge, Status

WALKS = {"up", "down", "left", "right"}


@dataclass
class ExecResult:
    executed: list[str]
    outcome: str         # ok | aborted:danger | died | level_complete
    status: Status
    detail: str = ""
    infotrons_collected: int = 0
    start: tuple[int, int] = (0, 0)

    def summary(self) -> str:
        """One line for the model's history and the logs."""
        s = self.status
        text = (f"{self.outcome}: ran {len(self.executed)} moves, Murphy {self.start} -> {s.murphy}, "
                f"collected {self.infotrons_collected}, infotrons needed {s.infotrons_left}")
        if self.start == s.murphy and self.executed and self.outcome == "ok":
            text += " (Murphy ended where he started: try a different route)"
        return text + (f". {self.detail}" if self.detail else "")


@dataclass
class Executor:
    main: Bridge
    shadow: Bridge | None
    level: int
    seed: int | None = None
    ticks_per_move: int = 8
    lookahead_moves: int = 3
    committed: list[str] = field(default_factory=list)
    status: Status | None = None

    def start(self) -> Status:
        """(Re)load the level on both engines and clear the input log."""
        self.committed = []
        status = self.main.load_level(self.level, seed=self.seed)
        if self.shadow is not None:
            self.shadow.load_level(self.level, seed=self.seed)
        self.status = status
        return status

    def run(self, moves: list[str]) -> ExecResult:
        start = self.status
        safe, detail = self._safe_prefix(moves)
        executed: list[str] = []
        blocked: list[str] = []
        outcome = "ok"
        status = start
        for i, move in enumerate(moves[:safe]):
            before = status.murphy
            status = self.main.step(move, self.ticks_per_move)
            executed.append(move)
            if move in WALKS and status.murphy == before:
                blocked.append(f"{i + 1} '{move}'")
            self.committed.append(move)
            if status.won:
                outcome = "level_complete"
                break
            if not status.alive:
                outcome = "died"
                break
        else:
            if safe < len(moves):
                outcome = "aborted:danger"
        self.status = status
        self._resync_shadow()
        notes = []
        if blocked:
            notes.append(f"Moves that did not move Murphy (blocked): {', '.join(blocked)}")
        if outcome == "aborted:danger":
            notes.append(detail)
        return ExecResult(
            executed=executed, outcome=outcome, status=status,
            detail=". ".join(notes),
            infotrons_collected=start.infotrons_left - status.infotrons_left,
            start=start.murphy,
        )

    # --- shadow -----------------------------------------------------------

    def _replay(self, extra: list[str]) -> Status:
        """Shadow = level start + committed inputs + extra. Returns its status
        at the first death or win, else at the end."""
        status = self.shadow.load_level(self.level, seed=self.seed)
        for move, group in groupby(self.committed):
            status = self.shadow.step(move, self.ticks_per_move * len(list(group)))
        for move in extra:
            status = self.shadow.step(move, self.ticks_per_move)
            if status.won or not status.alive:
                break
        return status

    def _resync_shadow(self) -> None:
        if self.shadow is not None:
            self._replay([])

    def _survives(self, moves: list[str]) -> Status:
        return self._replay(moves + ["wait"] * self.lookahead_moves)

    def _safe_prefix(self, moves: list[str]) -> tuple[int, str]:
        """Length of the longest prefix after which Murphy is still alive
        `lookahead_moves` waits later, and a reason when it is shorter."""
        if self.shadow is None:
            return len(moves), ""
        # Shadow is in sync with main here, so run the plan straight on it.
        for i, move in enumerate(moves + ["wait"] * self.lookahead_moves):
            status = self.shadow.step(move, self.ticks_per_move)
            if status.won:
                return min(i + 1, len(moves)), ""
            if not status.alive:
                break
        else:
            return len(moves), ""

        fatal = i  # index into moves + waits where Murphy died
        # moves[:fatal] leaves the fatal move out; if he died during the trailing
        # waits, the full plan is already known to be unsafe.
        first = fatal if fatal < len(moves) else len(moves) - 1
        for k in range(first, -1, -1):
            s = self._survives(moves[:k])
            if s.alive or s.won:
                reason = (f"Stopped after {k} of {len(moves)} moves: move {k + 1} '{moves[k]}' "
                          f"would lead to Murphy getting killed (a falling object or an enemy)")
                return k, reason
        # Murphy dies even standing still: nothing to save, run the plan as given.
        return len(moves), ""
