"""Adapter for any OpenAI-compatible chat completions endpoint
(OpenAI, OpenRouter, Groq, vLLM, LM Studio, Ollama's /v1, ...)."""
from __future__ import annotations

import os

import openai

from .base import PLAN_JSON_SCHEMA, AdapterBase, ProviderError, RawReply


class OpenAICompatProvider(AdapterBase):
    name = "openai_compat"

    def __init__(self, model: str, base_url: str | None = None, api_key_env: str = "OPENAI_API_KEY",
                 timeout_s: float = 20, temperature: float = 0.2,
                 response_format: str = "json_schema", client: openai.OpenAI | None = None, **_):
        """response_format: "json_schema" (structured output) or "json_object"
        for endpoints that only support plain JSON mode."""
        self.model = model
        self.temperature = temperature
        if response_format == "json_schema":
            self.response_format = {
                "type": "json_schema",
                "json_schema": {"name": "plan", "schema": PLAN_JSON_SCHEMA},
            }
        elif response_format == "json_object":
            self.response_format = {"type": "json_object"}
        else:
            raise ValueError(f"response_format must be json_schema or json_object, got {response_format!r}")
        # Local servers often need no key; the SDK still requires a non-empty one.
        api_key = os.environ.get(api_key_env) or "not-needed"
        self.client = client or openai.OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=0)

    def _request(self, system: str, user: str) -> RawReply:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                response_format=self.response_format,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except openai.APIError as e:
            raise ProviderError(f"openai_compat: {type(e).__name__}: {e}") from e
        content = resp.choices[0].message.content if resp.choices else ""
        usage = resp.usage
        return RawReply(
            content=content or "",
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )
