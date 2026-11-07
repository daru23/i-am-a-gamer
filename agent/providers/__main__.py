"""Ask the configured provider for one plan on a state file.

    uv run python -m agent.providers --config config.yaml
    uv run python -m agent.providers --config config.yaml --state tests/fixtures/sample_grid.txt
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import yaml

from . import load_provider

DEFAULT_STATE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sample_grid.txt"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--state", default=str(DEFAULT_STATE))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    config = yaml.safe_load(Path(args.config).read_text())
    provider = load_provider(config)
    plan, stats = provider.decide(Path(args.state).read_text(), notes="", history=[])
    print(json.dumps({
        "plan": plan.model_dump(),
        "provider": stats.provider,
        "model": stats.model,
        "latency_ms": round(stats.latency_ms),
        "input_tokens": stats.input_tokens,
        "output_tokens": stats.output_tokens,
        "valid": stats.valid,
        **stats.extra,
    }, indent=2))
    return 0 if stats.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
