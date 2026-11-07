# AI Plays Supaplex

An AI agent plays Supaplex (via a patched OpenSupaplex) for disclosed AI-played streams.
The full phased plan, with a prompt per phase, is in docs/PLAN.md.

## Goal
- 2-hour streams of smooth, normal-speed gameplay captured in OBS, a few minutes behind real time.
- Disclosure: "AI-played" in stream title/tags and a small static on-screen label. No reasoning overlay.
- First milestone: one level solved end to end on a local Ollama model.

## Architecture
- engine/ : OpenSupaplex fork (git submodule). Patched to run headless, step N ticks on command,
  and expose state over a Unix socket (JSON lines). Keep patches small and in one file where possible.
- agent/ : Python. Loop = read state -> ask provider for a plan -> execute moves tick by tick ->
  abort plan on danger -> log.
- agent/providers/ : one adapter per backend (ollama, claude, openai_compat) behind the Provider
  protocol in base.py. Game logic never lives in an adapter.
- replay/ : second engine instance, windowed, normal speed, replays the input log as it grows.
- bench/ : same levels + seed across providers, CSV scorecard.

## Rules
- The model sees the level as a text grid + legend, never screenshots.
- Model output is always JSON: {"reasoning": str, "moves": [str]}. Validate against the schema;
  on failure retry once, then fall back to a single "wait" move.
- The model is chosen only in config.yaml. No provider names hard-coded elsewhere.
- Secrets come from .env, never committed.
- Determinism matters: fixed RNG seed per level; replay must match the headless run exactly.
- Log every model call: provider, model, latency ms, tokens, plan length, outcome.

## Commands
- Build engine: scripts/build_engine.sh (needs `brew install sdl2 sdl2_mixer`); run from build/engine/
- Run agent: uv run python -m agent --config config.yaml --level 1
- Tests: uv run pytest (engine tests are skipped until the engine is built)
- Engine bridge: engine_patch/ (patch + bridge.c), API in docs/engine-notes.md
