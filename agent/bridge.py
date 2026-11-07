"""Client for the engine's Unix-socket JSON-lines API (engine_patch/bridge.c).

`Bridge.launch` starts an engine process and connects to it; `Bridge.connect`
attaches to one that is already running. Moves use the names from
agent.providers.base.Move.
"""
from __future__ import annotations

import json
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# UserInput values in the engine (supaplex.c, enum UserInput)
MOVE_INPUTS: dict[str, int] = {
    "wait": 0,
    "up": 1,
    "left": 2,
    "down": 3,
    "right": 4,
    "snap_up": 5,
    "snap_left": 6,
    "snap_down": 7,
    "snap_right": 8,
    "space": 9,  # space alone: drops a red disk
}


class BridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Status:
    level: int
    tick: int
    alive: bool
    won: bool
    over: bool
    infotrons_left: int
    murphy: tuple[int, int]
    rng: int
    ticks_run: int = 0

    @classmethod
    def from_reply(cls, reply: dict) -> Status:
        return cls(
            level=reply["level"],
            tick=reply["tick"],
            alive=reply["alive"],
            won=reply["won"],
            over=reply["over"],
            infotrons_left=reply["infotrons_left"],
            murphy=(reply["murphy"]["x"], reply["murphy"]["y"]),
            rng=reply["rng"],
            ticks_run=reply.get("ticks_run", 0),
        )


@dataclass(frozen=True)
class GameState:
    status: Status
    gravity: bool
    width: int
    height: int
    tiles: list[list[int]]   # [y][x] LevelTileType values (globals.h)
    states: list[list[int]]  # [y][x] per-tile animation/movement state, 0 = at rest

    @classmethod
    def from_reply(cls, reply: dict) -> GameState:
        w, h = reply["width"], reply["height"]
        rows = lambda flat: [flat[y * w:(y + 1) * w] for y in range(h)]  # noqa: E731
        return cls(
            status=Status.from_reply(reply),
            gravity=reply["gravity"],
            width=w,
            height=h,
            tiles=rows(reply["tiles"]),
            states=rows(reply["states"]),
        )


class Bridge:
    def __init__(self, sock: socket.socket, process: subprocess.Popen | None = None):
        self._sock = sock
        self._file = sock.makefile("rw", encoding="utf-8", newline="\n")
        self.process = process

    @classmethod
    def connect(cls, socket_path: str | Path, timeout_s: float = 10.0,
                process: subprocess.Popen | None = None) -> Bridge:
        deadline = time.monotonic() + timeout_s
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(str(socket_path))
                sock.settimeout(timeout_s)
                return cls(sock, process)
            except (FileNotFoundError, ConnectionRefusedError):
                sock.close()
                if process is not None and process.poll() is not None:
                    raise BridgeError(f"engine exited with code {process.returncode} before accepting connections")
                if time.monotonic() > deadline:
                    raise BridgeError(f"no engine listening on {socket_path}")
                time.sleep(0.05)

    @classmethod
    def launch(cls, binary: str | Path, socket_path: str | Path, seed: int = 0,
               headless: bool = True, timeout_s: float = 10.0) -> Bridge:
        """Start an engine process (cwd = the binary's dir, where resources live) and connect."""
        binary = Path(binary).resolve()
        socket_path = Path(socket_path).absolute()
        args = [str(binary), "--bridge-socket", str(socket_path), "--bridge-seed", str(seed)]
        if headless:
            args.append("--headless")
        # stderr passes through so engine errors show up in the agent's console
        process = subprocess.Popen(args, cwd=binary.parent, stdout=subprocess.DEVNULL)
        try:
            return cls.connect(socket_path, timeout_s, process)
        except BaseException:
            process.kill()
            raise

    @classmethod
    def from_config(cls, config: dict) -> Bridge:
        engine = config["engine"]
        return cls.launch(engine["binary"], engine["socket"], seed=engine.get("seed", 0),
                          headless=engine.get("headless", True))

    def _call(self, request: dict) -> dict:
        self._file.write(json.dumps(request) + "\n")
        self._file.flush()
        line = self._file.readline()
        if not line:
            raise BridgeError("engine closed the connection")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise BridgeError(reply.get("error", "unknown error"))
        return reply

    def load_level(self, level: int, seed: int | None = None) -> Status:
        request = {"cmd": "load_level", "level": level}
        if seed is not None:
            request["seed"] = seed
        return Status.from_reply(self._call(request))

    def get_state(self) -> GameState:
        return GameState.from_reply(self._call({"cmd": "get_state"}))

    def step(self, move: str | int, n_ticks: int = 1) -> Status:
        """Hold `move` for n_ticks ticks. Stops early when the level ends."""
        value = MOVE_INPUTS[move] if isinstance(move, str) else move
        return Status.from_reply(self._call({"cmd": "step", "input": value, "n_ticks": n_ticks}))

    def close(self) -> None:
        """Ask the engine to quit (if we launched it) and release the socket."""
        try:
            if self.process is not None and self.process.poll() is None:
                self._call({"cmd": "quit"})
        except (OSError, BridgeError):
            pass
        finally:
            self._file.close()
            self._sock.close()
            if self.process is not None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()

    def __enter__(self) -> Bridge:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def default_binary() -> Path:
    return Path(__file__).resolve().parent.parent / "build" / "engine" / "opensupaplex"


__all__ = ["Bridge", "BridgeError", "GameState", "MOVE_INPUTS", "Status", "default_binary"]
