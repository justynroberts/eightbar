"""The agent loop and the backend adapters."""

from __future__ import annotations

from typing import Any

import pytest
from fake_live import FakeBridge

from ableton_ai.agent import Agent
from ableton_ai.llm import make_backend
from ableton_ai.llm.base import AssistantTurn, ToolCall
from ableton_ai.llm.claude_cli_backend import ClaudeCLIBackend


class ScriptedBackend:
    """Replays a fixed list of turns, so the loop can be tested deterministically."""

    name = "scripted"

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self.turns = list(turns)
        self.seen: list[list[dict[str, Any]]] = []

    def complete(self, system, messages, tools):
        self.seen.append([dict(m) for m in messages])
        if not self.turns:
            return AssistantTurn(text="done", tool_calls=[])
        return self.turns.pop(0)

    def record_assistant(self, messages, turn):
        messages.append({"role": "assistant", "content": turn.text or "[tools]"})

    def record_results(self, messages, results):
        messages.append({
            "role": "user",
            "content": "\n".join(f"{r.name}: {r.content}" for r in results),
        })


def make_agent(turns: list[AssistantTurn]) -> Agent:
    return Agent(backend=ScriptedBackend(turns), bridge=FakeBridge())


# ------------------------------------------------------------ the loop

def test_a_plain_reply_ends_the_turn():
    agent = make_agent([AssistantTurn(text="Set is empty.")])
    kinds = [e.kind for e in agent.run("what have I got?")]
    assert kinds == ["text", "done"]


def test_tool_calls_run_then_the_loop_continues():
    agent = make_agent([
        AssistantTurn(tool_calls=[
            ToolCall("1", "create_track", {"name": "Bass", "role": "bass"})
        ]),
        AssistantTurn(text="Made a bass track."),
    ])
    events = list(agent.run("add a bass track"))
    kinds = [e.kind for e in events]
    assert kinds == ["tool_start", "tool_end", "text", "done"]
    assert events[1].data["ok"] is True
    assert agent.bridge.tracks[0]["name"] == "Bass"


def test_parallel_tool_calls_all_run_in_one_step():
    agent = make_agent([
        AssistantTurn(tool_calls=[
            ToolCall("1", "create_track", {"name": "Kick", "role": "kick"}),
            ToolCall("2", "create_track", {"name": "Bass", "role": "bass"}),
        ]),
        AssistantTurn(text="Done."),
    ])
    list(agent.run("two tracks"))
    assert [t["name"] for t in agent.bridge.tracks] == ["Kick", "Bass"]


def test_a_failing_tool_is_reported_back_so_the_model_can_recover():
    agent = make_agent([
        AssistantTurn(tool_calls=[ToolCall("1", "create_drum_clip",
                                           {"track_index": 42})]),
        AssistantTurn(text="Recovered."),
    ])
    events = list(agent.run("drums"))
    failure = next(e for e in events if e.kind == "tool_end")
    assert failure.data["ok"] is False
    # The error text must reach the transcript, not just the UI.
    transcript = agent.backend.seen[-1]
    assert "out of range" in str(transcript[-1]["content"])


def test_an_unknown_tool_does_not_kill_the_loop():
    agent = make_agent([
        AssistantTurn(tool_calls=[ToolCall("1", "make_coffee", {})]),
        AssistantTurn(text="Sorry."),
    ])
    kinds = [e.kind for e in agent.run("coffee")]
    assert kinds[-1] == "done"


def test_backend_failure_surfaces_as_an_error_event():
    class Broken(ScriptedBackend):
        def complete(self, system, messages, tools):
            raise RuntimeError("no credentials")

    agent = Agent(backend=Broken([]), bridge=FakeBridge())
    events = list(agent.run("hello"))
    assert events[-1].kind == "error"
    assert "no credentials" in events[-1].data["message"]


def test_the_loop_stops_rather_than_spinning_forever():
    """A model that only ever calls tools must be cut off, not loop for ever."""
    class Looping(ScriptedBackend):
        def complete(self, system, messages, tools):
            return AssistantTurn(tool_calls=[ToolCall("x", "get_song_state", {})])

    agent = Agent(backend=Looping([]), bridge=FakeBridge(), max_iterations=3)
    events = list(agent.run("go"))
    assert events[-1].kind == "error"
    assert "stopped after 3 steps" in events[-1].data["message"]


def test_conversation_persists_across_turns_until_reset():
    agent = make_agent([AssistantTurn(text="one"), AssistantTurn(text="two")])
    list(agent.run("first"))
    list(agent.run("second"))
    assert len(agent.messages) == 4
    agent.reset()
    assert agent.messages == []


# --------------------------------------------------- claude -p protocol

@pytest.fixture
def cli() -> ClaudeCLIBackend:
    return ClaudeCLIBackend()


def test_bare_protocol_object_parses(cli):
    turn = cli._parse('{"say": "hi", "tools": []}')
    assert turn.text == "hi"
    assert turn.tool_calls == []


def test_fenced_protocol_object_parses(cli):
    turn = cli._parse(
        'Here you go:\n```json\n{"say": "ok", "tools": '
        '[{"name": "set_tempo", "input": {"tempo": 128}}]}\n```'
    )
    assert turn.tool_calls[0].name == "set_tempo"
    assert turn.tool_calls[0].input == {"tempo": 128}


def test_protocol_object_with_surrounding_prose_parses(cli):
    turn = cli._parse('Sure. {"say": "", "tools": [{"name": "get_song_state", '
                      '"input": {}}]} Let me know.')
    assert turn.tool_calls[0].name == "get_song_state"


def test_plain_prose_is_treated_as_a_reply_not_a_crash(cli):
    turn = cli._parse("I could not do that.")
    assert turn.text == "I could not do that."
    assert turn.tool_calls == []


def test_malformed_tool_entries_are_skipped(cli):
    turn = cli._parse('{"say": "x", "tools": [{"input": {}}, '
                      '{"name": "set_tempo", "input": {"tempo": 120}}]}')
    assert len(turn.tool_calls) == 1


def test_cli_output_envelope_is_unwrapped(cli):
    assert cli._unwrap('{"result": "hello", "cost": 1}') == "hello"
    assert cli._unwrap("not json") == "not json"


def test_tool_calls_get_distinct_ids(cli):
    turn = cli._parse('{"tools": [{"name": "a", "input": {}}, '
                      '{"name": "b", "input": {}}]}')
    assert len({c.id for c in turn.tool_calls}) == 2


# -------------------------------------------------------- backend choice

def test_explicit_backend_selection_does_not_need_a_key():
    assert make_backend("claude-cli").name == "claude-cli"


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="unknown backend"):
        make_backend("gpt")
