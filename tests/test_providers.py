"""Provider layer tests. All offline: backends are replaced by fake clients."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agent.providers import load_provider
from agent.providers.base import WAIT_PLAN, PLAN_JSON_SCHEMA, ProviderError, Turn, Plan
from agent.providers.claude import TOOL_NAME, ClaudeProvider
from agent.providers.factory import CallLogger, ManagedProvider, build_adapter
from agent.providers.ollama import OllamaProvider
from agent.providers.openai_compat import OpenAICompatProvider

STATE = (Path(__file__).parent / "fixtures" / "sample_grid.txt").read_text()
GOOD = {"reasoning": "eat base then grab the infotron", "moves": ["right", "down", "right"]}
BAD = {"reasoning": "x", "moves": ["jump"]}


# --- fakes ---------------------------------------------------------------

def ollama_with(replies: list, seen: list | None = None) -> OllamaProvider:
    replies = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": json.dumps(reply)},
            "prompt_eval_count": 100, "eval_count": 20,
        })

    client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return OllamaProvider(model="qwen-test", seed=1234, client=client)


class FakeAnthropic:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        from anthropic.resources.messages import Messages
        inspect.signature(Messages.create).bind(None, **kwargs)  # same args the real SDK takes
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", name=TOOL_NAME, input=reply)],
            usage=SimpleNamespace(input_tokens=200, output_tokens=30),
        )


class FakeOpenAI:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        from openai.resources.chat.completions import Completions
        inspect.signature(Completions.create).bind(None, **kwargs)  # same args the real SDK takes
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        content =reply if isinstance(reply, str) else json.dumps(reply)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=150, completion_tokens=25),
        )


class StubProvider:
    def __init__(self, name, result):
        self.name, self.model, self.result, self.calls = name, f"{name}-model", result, 0

    def decide(self, state, notes, history):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        from agent.providers.base import CallStats
        valid = self.result is not WAIT_PLAN
        return self.result, CallStats(self.name, self.model, 5.0, 10, 5, valid=valid)


# --- adapters ------------------------------------------------------------

def test_ollama_sends_schema_and_counts_tokens():
    seen = []
    plan, stats = ollama_with([GOOD], seen).decide(STATE, "", [])
    assert plan.moves == GOOD["moves"]
    assert stats.valid and stats.input_tokens == 100 and stats.output_tokens == 20
    body = seen[0]
    assert body["format"] == PLAN_JSON_SCHEMA
    assert body["stream"] is False and body["options"]["seed"] == 1234
    assert "#M:::O..E#" in body["messages"][1]["content"]


def test_ollama_retries_once_then_succeeds():
    seen = []
    plan, stats = ollama_with([BAD, GOOD], seen).decide(STATE, "", [])
    assert plan.moves == GOOD["moves"] and stats.valid
    assert stats.extra["attempts"] == 2
    assert stats.input_tokens == 200  # summed over both attempts
    assert "previous reply was invalid" in seen[1]["messages"][1]["content"]


def test_ollama_invalid_twice_returns_wait():
    plan, stats = ollama_with([BAD, BAD]).decide(STATE, "", [])
    assert plan == WAIT_PLAN and not stats.valid and "moves" in stats.extra["error"]


def test_ollama_timeout_raises_provider_error():
    with pytest.raises(ProviderError):
        ollama_with([httpx.ReadTimeout("slow")]).decide(STATE, "", [])


def test_claude_forces_tool_use():
    fake = FakeAnthropic([GOOD])
    plan, stats = ClaudeProvider(model="claude-test", client=fake).decide(STATE, "", [])
    assert plan.moves == GOOD["moves"] and stats.input_tokens == 200
    call = fake.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert call["tools"][0]["input_schema"] == PLAN_JSON_SCHEMA


def test_claude_without_key_raises_provider_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        ClaudeProvider(model="claude-test").decide(STATE, "", [])


def test_claude_retry_then_wait():
    fake = FakeAnthropic([BAD, BAD])
    plan, stats = ClaudeProvider(model="claude-test", client=fake).decide(STATE, "", [])
    assert plan == WAIT_PLAN and len(fake.calls) == 2


def test_openai_compat_json_schema():
    fake = FakeOpenAI([GOOD])
    plan, stats = OpenAICompatProvider(model="gpt-test", client=fake).decide(STATE, "", [])
    assert plan.moves == GOOD["moves"] and stats.output_tokens == 25
    assert fake.calls[0]["response_format"]["type"] == "json_schema"


def test_openai_compat_non_json_reply_retried():
    fake = FakeOpenAI(["sure! go right", GOOD])
    plan, stats = OpenAICompatProvider(model="gpt-test", response_format="json_object",
                                       client=fake).decide(STATE, "", [])
    assert plan.moves == GOOD["moves"] and stats.extra["attempts"] == 2
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


def test_prompt_includes_notes_and_history():
    seen = []
    history = [Turn(state="...", plan=Plan(reasoning="r", moves=["left"]), outcome="aborted:danger")]
    ollama_with([GOOD], seen).decide(STATE, "zonk above exit falls", history)
    user = seen[0]["messages"][1]["content"]
    assert "zonk above exit falls" in user and "left -> aborted:danger" in user


# --- factory, fallback, logging -----------------------------------------

def test_build_adapter_from_config():
    p = build_adapter({"name": "ollama", "model": "m", "base_url": "http://x", "timeout_s": 3}, seed=7)
    assert isinstance(p, OllamaProvider) and p.model == "m" and p.seed == 7


def test_build_adapter_rejects_unknown():
    with pytest.raises(ValueError, match="unknown provider"):
        build_adapter({"name": "nope", "model": "m"})


def test_load_provider_reads_config_yaml(tmp_path):
    import yaml
    config = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
    config["logging"]["dir"] = str(tmp_path)
    provider = load_provider(config)
    assert provider.primary.name == config["provider"]["name"]
    assert provider.fallback.name == config["fallback"]["name"]


def test_fallback_on_primary_error(tmp_path):
    primary = StubProvider("p", ProviderError("timeout"))
    fallback = StubProvider("f", Plan(**GOOD))
    plan, stats = ManagedProvider(primary, fallback, CallLogger(tmp_path)).decide(STATE, "", [])
    assert plan.moves == GOOD["moves"] and stats.provider == "f"
    lines = [json.loads(l) for l in (tmp_path / "model_calls.jsonl").read_text().splitlines()]
    assert [(l["role"], l["outcome"]) for l in lines] == [("primary", "error"), ("fallback", "ok")]
    assert lines[1]["plan_len"] == 3 and lines[1]["input_tokens"] == 10


def test_fallback_on_invalid_output():
    primary, fallback = StubProvider("p", WAIT_PLAN), StubProvider("f", Plan(**GOOD))
    plan, _ = ManagedProvider(primary, fallback).decide(STATE, "", [])
    assert plan.moves == GOOD["moves"]


def test_primary_ok_skips_fallback():
    primary, fallback = StubProvider("p", Plan(**GOOD)), StubProvider("f", Plan(**GOOD))
    ManagedProvider(primary, fallback).decide(STATE, "", [])
    assert fallback.calls == 0


def test_everything_fails_returns_wait():
    primary = StubProvider("p", ProviderError("down"))
    fallback = StubProvider("f", ProviderError("down too"))
    plan, stats = ManagedProvider(primary, fallback).decide(STATE, "", [])
    assert plan == WAIT_PLAN and not stats.valid


def test_no_fallback_error_returns_wait():
    plan, stats = ManagedProvider(StubProvider("p", ProviderError("down"))).decide(STATE, "", [])
    assert plan == WAIT_PLAN and not stats.valid
