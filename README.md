# AI Plays Supaplex

An AI agent plays Supaplex for disclosed "AI-played" streams. The model backend is pluggable
(local Ollama, Claude, or any OpenAI-compatible provider) and chosen in `config.yaml`.

## Start
1. `git submodule update --init` (fetches the OpenSupaplex engine into `engine/`)
2. `cp .env.example .env` and add keys if you use a hosted provider
3. `brew install uv sdl2 sdl2_mixer`, then `uv sync && uv run pytest`
4. `scripts/build_engine.sh` to build the engine (engine tests are skipped until it exists)
5. Run `claude` in this folder and paste the Phase 0 prompt from `docs/PLAN.md`
