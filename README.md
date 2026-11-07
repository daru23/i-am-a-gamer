# AI Plays Supaplex

An AI agent plays Supaplex for disclosed "AI-played" streams. The model backend is pluggable
(local Ollama, Claude, or any OpenAI-compatible provider) and chosen in `config.yaml`.

## Start
1. `git init && git submodule add https://github.com/sergiou87/open-supaplex engine`
   (check the URL first; any OpenSupaplex fork works)
2. `cp .env.example .env` and add keys if you use a hosted provider
3. `uv sync && uv run pytest`
4. Run `claude` in this folder and paste the Phase 0 prompt from `docs/PLAN.md`
