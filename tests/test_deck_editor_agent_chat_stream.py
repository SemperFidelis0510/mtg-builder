"""Tests for the deck-editor chat agent's tool-loop and safety behavior.

Covers:
- Natural completion (single round, no tool calls).
- Multi-round tool use that exceeds the historical 10-round cap.
- MAX_TOKENS mid-stream, auto-continuing to the next call.
- Deck-mutation approval pause/resume.
- Safety-ceiling stop yields a clear error, not a normal done.
"""
from __future__ import annotations

from typing import Any, Callable

import pytest

from src.deck_editor import agent as agent_mod

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Fake Gemini streaming client
# ---------------------------------------------------------------------------


class _FakeFunctionCall:
    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.args = args


class _FakePart:
    def __init__(self, function_call: _FakeFunctionCall | None = None, text: str | None = None) -> None:
        self.function_call = function_call
        self.text = text


class _FakeContent:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.parts = parts
        self.role = "model"


class _FakeCandidate:
    def __init__(self, content: _FakeContent | None = None, finish_reason: Any = None) -> None:
        self.content = content
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, text: str | None = None, candidates: list[_FakeCandidate] | None = None) -> None:
        self.text = text
        self.candidates = candidates or []


def _text_chunk(text: str, finish_reason: Any = None) -> _FakeChunk:
    return _FakeChunk(text=text, candidates=[_FakeCandidate(content=None, finish_reason=finish_reason)])


def _fc_chunk(name: str, args: dict, finish_reason: Any = None) -> _FakeChunk:
    fc = _FakeFunctionCall(name, args)
    part = _FakePart(function_call=fc)
    return _FakeChunk(
        text=None,
        candidates=[_FakeCandidate(content=_FakeContent(parts=[part]), finish_reason=finish_reason)],
    )


def _stop_chunk(text: str = "") -> _FakeChunk:
    return _text_chunk(text, finish_reason=agent_mod.types.FinishReason.STOP)


class _FakeModels:
    def __init__(self, rounds: list[list[_FakeChunk] | Callable[..., list[_FakeChunk]]]) -> None:
        # Each element is either a list of chunks to yield for that call, or a
        # callable that returns such a list given (model, contents, config).
        self._rounds = list(rounds)
        self._call_idx = 0
        self.contents_history: list[list] = []

    async def generate_content_stream(self, *, model: str, contents: list, config: Any):
        self.contents_history.append(list(contents))
        idx = self._call_idx
        self._call_idx += 1
        if idx >= len(self._rounds):
            raise AssertionError(
                f"FakeModels: generate_content_stream called {idx + 1} times, "
                f"only {len(self._rounds)} rounds configured"
            )
        r = self._rounds[idx]
        chunks = r(model, contents, config) if callable(r) else r

        async def _agen():
            for c in chunks:
                yield c

        return _agen()


class _FakeAio:
    def __init__(self, rounds) -> None:
        self.models = _FakeModels(rounds)


class _FakeClient:
    def __init__(self, rounds) -> None:
        self.aio = _FakeAio(rounds)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def patched_agent(monkeypatch, tmp_path):
    """Isolate the agent module from real API key, disk, and tool execution."""
    monkeypatch.setattr(agent_mod, "CONVERSATIONS_DIR", tmp_path / "conv")
    monkeypatch.setattr(agent_mod, "load_api_key", lambda: "test-key")

    def _fake_execute(name: str, args: dict) -> str:
        return f"result:{name}"

    monkeypatch.setattr(agent_mod, "execute_tool_call", _fake_execute)
    return monkeypatch


def _install_client(monkeypatch, rounds) -> _FakeClient:
    fake = _FakeClient(rounds)
    monkeypatch.setattr(agent_mod, "_get_client", lambda _key: fake)
    return fake


async def _collect(gen) -> list[dict]:
    out: list[dict] = []
    async for e in gen:
        out.append(e)
    return out


def _dummy_deck() -> dict:
    return {"deck": {"name": "T", "format": "commander", "colors": []}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_natural_completion_yields_done(patched_agent):
    _install_client(patched_agent, [[_stop_chunk("Hello, world.")]])
    conv = agent_mod.create_conversation()

    events = await _collect(agent_mod.chat_stream(conv, "Hi", _dummy_deck()))

    assert any(e["type"] == "text_delta" and e["content"] == "Hello, world." for e in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["conversation_id"] == conv["id"]
    assert not any(e["type"] == "error" for e in events)


async def test_tool_rounds_beyond_old_cap_completes_normally(patched_agent):
    # Historical MAX_TOOL_ROUNDS was 10; this exercises well beyond that.
    total_rounds = 15
    rounds: list = [[_fc_chunk("get_card_info", {"card_names": f"c{i}"})] for i in range(total_rounds)]
    rounds.append([_stop_chunk("all done")])
    _install_client(patched_agent, rounds)

    conv = agent_mod.create_conversation()
    events = await _collect(agent_mod.chat_stream(conv, "search a lot", _dummy_deck()))

    tool_calls = [e for e in events if e["type"] == "tool_call"]
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_calls) == total_rounds
    assert len(tool_results) == total_rounds
    assert events[-1]["type"] == "done"
    assert not any(e["type"] == "error" for e in events)


async def test_max_tokens_continues_transparently(patched_agent):
    _install_client(
        patched_agent,
        [
            [_text_chunk("Part one, ", finish_reason=agent_mod.types.FinishReason.MAX_TOKENS)],
            [_stop_chunk("part two.")],
        ],
    )
    conv = agent_mod.create_conversation()

    events = await _collect(agent_mod.chat_stream(conv, "long answer", _dummy_deck()))

    text_deltas = [e["content"] for e in events if e["type"] == "text_delta"]
    assert text_deltas == ["Part one, ", "part two."]
    assert events[-1]["type"] == "done"
    assert not any(e["type"] == "error" for e in events)


async def test_approval_pause_and_resume(patched_agent):
    _install_client(
        patched_agent,
        [
            [_fc_chunk("append_cards_to_deck", {"card_names": "Sol Ring", "board": "main"})],
            [_stop_chunk("Added.")],
        ],
    )
    conv = agent_mod.create_conversation()

    seen: list[dict] = []
    async for e in agent_mod.chat_stream(conv, "add Sol Ring", _dummy_deck()):
        seen.append(e)
        if e["type"] == "tool_call":
            assert e.get("requires_approval") is True
            await agent_mod.resolve_tool_approval(e["approval_id"], True)

    types_seen = [e["type"] for e in seen]
    assert types_seen.count("tool_call") == 1
    assert types_seen.count("tool_result") == 1
    assert seen[-1]["type"] == "done"


async def test_safety_limit_yields_error_and_no_done(monkeypatch, patched_agent):
    monkeypatch.setattr(agent_mod, "SAFETY_MAX_TOOL_ROUNDS", 3)
    rounds = [
        [_fc_chunk("get_card_info", {"card_names": "a"})],
        [_fc_chunk("get_card_info", {"card_names": "b"})],
        [_fc_chunk("get_card_info", {"card_names": "c"})],
    ]
    _install_client(patched_agent, rounds)
    conv = agent_mod.create_conversation()

    events = await _collect(agent_mod.chat_stream(conv, "loop", _dummy_deck()))

    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_calls) == 3
    assert not any(e["type"] == "done" for e in events)
    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert "safety limit" in error_events[0]["message"].lower()
