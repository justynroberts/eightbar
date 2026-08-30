"""Backend-agnostic shapes for one assistant turn.

The agent loop only ever sees these types, so the Anthropic API backend (native
tool use) and the `claude -p` CLI backend (a JSON text protocol) are
interchangeable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass
class AssistantTurn:
    """What the model produced this turn."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class Backend(Protocol):
    """Anything that can take a conversation and produce one assistant turn."""

    name: str

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        ...

    def record_assistant(
        self, messages: list[dict[str, Any]], turn: AssistantTurn
    ) -> None:
        """Append the assistant turn to the transcript in this backend's format."""
        ...

    def record_results(
        self, messages: list[dict[str, Any]], results: list[ToolResult]
    ) -> None:
        """Append tool results to the transcript in this backend's format."""
        ...
