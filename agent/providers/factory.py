"""Build the provider named in config.yaml, wrapped with fallback and call logging.

config.yaml is the only place a provider is chosen. The registry below maps
config names to adapter classes and is the only place adapters are named.
"""
from __future__ import annotations

import importlib
import json
import logging
import time
from pathlib import Path

from dotenv import load_dotenv

from .base import WAIT_PLAN, CallStats, Plan, Provider, ProviderError, Turn

log = logging.getLogger(__name__)

REGISTRY = {
    "ollama": "agent.providers.ollama:OllamaProvider",
    "claude": "agent.providers.claude:ClaudeProvider",
    "openai_compat": "agent.providers.openai_compat:OpenAICompatProvider",
}

CALL_LOG = "model_calls.jsonl"


def build_adapter(spec: dict, seed: int | None = None) -> Provider:
    spec = dict(spec)
    name = spec.pop("name", None)
    if name not in REGISTRY:
        raise ValueError(f"unknown provider {name!r}; expected one of {sorted(REGISTRY)}")
    if "model" not in spec:
        raise ValueError(f"provider {name!r} needs a model in config.yaml")
    module_name, cls_name = REGISTRY[name].split(":")
    cls = getattr(importlib.import_module(module_name), cls_name)
    if seed is not None:
        spec.setdefault("seed", seed)
    return cls(**spec)


class CallLogger:
    """Appends one JSON line per model call to <logging.dir>/model_calls.jsonl."""

    def __init__(self, log_dir: str | Path | None):
        self.path = Path(log_dir) / CALL_LOG if log_dir else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, role: str, stats: CallStats, plan: Plan | None, outcome: str) -> None:
        record = {
            "ts": time.time(),
            "role": role,
            "provider": stats.provider,
            "model": stats.model,
            "latency_ms": round(stats.latency_ms, 1),
            "input_tokens": stats.input_tokens,
            "output_tokens": stats.output_tokens,
            "plan_len": len(plan.moves) if plan else 0,
            "outcome": outcome,  # ok | invalid | error
            **stats.extra,
        }
        log.info("model call %s", record)
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(record) + "\n")


class ManagedProvider:
    """Primary provider with an optional fallback.

    The fallback is used when the primary raises (timeout, connection, HTTP
    error) or gives invalid output twice. If everything fails, the plan is a
    single wait move. Every underlying call is logged."""

    def __init__(self, primary: Provider, fallback: Provider | None = None,
                 logger: CallLogger | None = None):
        self.primary = primary
        self.fallback = fallback
        self.logger = logger or CallLogger(None)
        self.name = primary.name
        self.model = primary.model

    def decide(self, state: str, notes: str, history: list[Turn]) -> tuple[Plan, CallStats]:
        last_stats = None
        for role, provider in (("primary", self.primary), ("fallback", self.fallback)):
            if provider is None:
                continue
            plan, stats = self._call(role, provider, state, notes, history)
            if plan is not None:
                return plan, stats
            last_stats = stats
        return WAIT_PLAN, last_stats

    def _call(self, role, provider, state, notes, history) -> tuple[Plan | None, CallStats]:
        start = time.perf_counter()
        try:
            plan, stats = provider.decide(state, notes, history)
        except ProviderError as e:
            stats = CallStats(provider=provider.name, model=provider.model,
                              latency_ms=(time.perf_counter() - start) * 1000,
                              valid=False, extra={"error": str(e)})
            self.logger.write(role, stats, None, "error")
            return None, stats
        stats.extra["role"] = role
        if not stats.valid:
            self.logger.write(role, stats, None, "invalid")
            return None, stats
        self.logger.write(role, stats, plan, "ok")
        return plan, stats


def load_provider(config: dict) -> ManagedProvider:
    """Build the provider stack from a parsed config.yaml dict."""
    load_dotenv()
    seed = config.get("engine", {}).get("seed")
    primary = build_adapter(config["provider"], seed=seed)
    fallback = build_adapter(config["fallback"], seed=seed) if config.get("fallback") else None
    log_dir = config.get("logging", {}).get("dir")
    return ManagedProvider(primary, fallback, CallLogger(log_dir))
