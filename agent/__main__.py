"""Play levels with the provider from config.yaml.

    uv run python -m agent --config config.yaml --level 1
    uv run python -m agent --config config.yaml --level 1-5
"""
from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path

import yaml

from .bridge import Bridge
from .loop import RunLog, play_level
from .providers import load_provider


def parse_levels(text: str) -> list[int]:
    if "-" in text:
        a, b = text.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(text)]


def shadow_socket(path: str) -> str:
    p = Path(path)
    return str(p.with_name(p.stem + "-shadow" + p.suffix))


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--level", default="1", help="level number or range, e.g. 1 or 1-5")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    config = yaml.safe_load(Path(args.config).read_text())
    engine = config["engine"]
    provider = load_provider(config)
    log = RunLog(config.get("logging", {}).get("dir"))
    log.event("run_start", f"provider {provider.name} / {provider.model}, log {log.path}",
              provider=provider.name, model=provider.model, levels=args.level,
              seed=engine.get("seed"))

    # A plain `kill` should shut the engines down too, like Ctrl-C does.
    signal.signal(signal.SIGTERM, _raise_interrupt)

    use_shadow = config.get("agent", {}).get("lookahead", True)
    main_bridge = Bridge.launch(engine["binary"], engine["socket"], seed=engine.get("seed", 0),
                                headless=engine.get("headless", True))
    shadow = (Bridge.launch(engine["binary"], shadow_socket(engine["socket"]),
                            seed=engine.get("seed", 0), headless=True) if use_shadow else None)
    results = []
    try:
        for level in parse_levels(args.level):
            results.append(play_level(level, provider, main_bridge, shadow, config, log))
    except KeyboardInterrupt:
        log.event("interrupted", "interrupted")
    finally:
        main_bridge.close()
        if shadow is not None:
            shadow.close()

    solved = sum(r.solved for r in results)
    log.event("run_end", f"solved {solved}/{len(results)} levels",
              solved=solved, levels=[r.__dict__ for r in results])
    return 0 if results and solved == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
