"""Ollama adapter: /api/chat with `format` set to the Plan JSON schema."""
from __future__ import annotations

import httpx

from .base import PLAN_JSON_SCHEMA, AdapterBase, ProviderError, RawReply


class OllamaProvider(AdapterBase):
    name = "ollama"

    def __init__(self, model: str, base_url: str = "http://localhost:11434",
                 timeout_s: float = 20, temperature: float = 0.2, seed: int | None = None,
                 keep_alive: str = "30m", client: httpx.Client | None = None, **_):
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.keep_alive = keep_alive
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout_s)

    def _request(self, system: str, user: str) -> RawReply:
        options: dict = {"temperature": self.temperature}
        if self.seed is not None:
            options["seed"] = self.seed
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": PLAN_JSON_SCHEMA,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": options,
        }
        try:
            r = self.client.post("/api/chat", json=body)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPStatusError as e:
            raise ProviderError(f"ollama HTTP {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"ollama: {type(e).__name__}: {e}") from e
        return RawReply(
            content=data.get("message", {}).get("content", ""),
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )
