# Plan and phase prompts

Work through these in order. Each phase ends with a check before the next starts.
Phases 1 and 2 can run in parallel, because Phase 2 starts on a fake game state.
Start a session with `claude` in the repo root, then paste the phase prompt.

## Phase 0: Spike the engine
Done when: OpenSupaplex builds, a level plays by hand, and the loop, input and RNG code are mapped.

> Read CLAUDE.md. Build the OpenSupaplex submodule in engine/ on this machine and fix any build
> issues. Then write docs/engine-notes.md mapping where the main game loop, input handling, level
> loading and RNG live, with file and line references. Don't change engine code yet.

## Phase 1: Control bridge
Done when: a Python script can load level 1, read the grid, send moves and step ticks headless,
and two runs with the same seed and inputs end identical.

> Using docs/engine-notes.md, add a minimal patch to the engine: a headless flag, a fixed-seed flag,
> and a Unix-socket JSON-lines API with commands load_level, get_state (grid, Murphy position,
> infotrons left, alive, tick) and step (input, n_ticks). Write agent/bridge.py as the client and a
> determinism test in tests/.

## Phase 2: Provider layer
Done when: the same fake state gets a valid plan from Ollama, Claude and one OpenAI-compatible
endpoint, switched only in config.yaml.

> agent/providers/base.py already defines the Provider protocol and Plan model. Implement
> ollama.py (format with JSON schema), claude.py (tool use for structured output) and
> openai_compat.py. Load the provider from config.yaml with timeout and fallback. On invalid output,
> retry once, then return a wait move. Log latency and tokens per call. Test against the sample grid
> in tests/fixtures/sample_grid.txt.

## Phase 3: Agent loop
Done when: a model clears level 1 on its own, and deaths and retries are logged.

> Write state.py (grid to text with legend, plus a local window around Murphy), executor.py (runs
> moves tick by tick, aborts if a zonk or enemy threatens Murphy) and loop.py tying bridge and
> provider together. Add per-level notes the model can update, and skip the level after N failed
> attempts from config.

## Phase 4: Replay and stream
Done when: a 2-hour offline run streams smoothly to a private test stream with no stalls.

> Make the agent append every input with its tick to an input log. Build replay/ to launch a
> windowed engine at normal speed with the same seed that reads the log as it grows, holds a
> configurable buffer before starting, and shows an intermission screen if the buffer runs out.

In OBS: window-capture the replay, add the "AI-played" text label and music, build an intermission scene.

## Phase 5: Benchmark
Done when: a scorecard compares providers on levels solved, deaths and seconds per decision.

> Write bench/ to run levels 1-10 with a fixed seed against each provider in a list and output a
> CSV scorecard.

## Risks
| Risk | Check | Fallback |
| --- | --- | --- |
| Hidden randomness, replay drifts | Determinism test in Phase 1 | Patch every RNG call to use the fixed seed |
| Model slower than real-time playback | Seconds per decision in the benchmark | Longer plans, faster model, bigger buffer |
| Local model gives bad JSON or weak plans | Invalid-output rate in logs | Schema retry, then fallback provider |
| Later levels too hard | Levels solved in benchmark | Skip after N attempts; stream early levels |
| API costs over 2 hours | Token totals per run | Console spending limit; Ollama for most turns |
| Licensing of game and levels | Before first public stream | Fan-made levels with clear permission |
| Platform rules on automated streams | Before first public stream | Clear AI disclosure in title, tags, on screen |
