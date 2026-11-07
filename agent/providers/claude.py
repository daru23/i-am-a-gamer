"""Claude adapter: forced tool use, so the plan arrives as structured tool input."""
from __future__ import annotations

import os

import anthropic

from .base import PLAN_JSON_SCHEMA, AdapterBase, ProviderError, RawReply

TOOL_NAME = "submit_plan"


class ClaudeProvider(AdapterBase):
    name = "claude"

    def __init__(self, model: str, timeout_s: float = 15, max_tokens: int = 1024,
                 client: anthropic.Anthropic | None = None, **_):
        self.model = model
        self.max_tokens = max_tokens
        # API key comes from ANTHROPIC_API_KEY (.env). Retries are ours, not the SDK's.
        self.missing_key = client is None and not os.environ.get("ANTHROPIC_API_KEY")
        self.client = client or anthropic.Anthropic(timeout=timeout_s, max_retries=0)
        self.tool = {
            "name": TOOL_NAME,
            "description": "Submit the move plan for Murphy.",
            "input_schema": PLAN_JSON_SCHEMA,
        }

    def _request(self, system: str, user: str) -> RawReply:
        if self.missing_key:
            raise ProviderError("claude: ANTHROPIC_API_KEY is not set (add it to .env)")
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=[self.tool],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as e:
            raise ProviderError(f"claude: {type(e).__name__}: {e}") from e
        content: str | dict = ""
        for block in msg.content:
            if block.type == "tool_use" and block.name == TOOL_NAME:
                content = block.input
                break
        return RawReply(
            content=content,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )
