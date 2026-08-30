"""Anthropic Messages API backend, using native tool use."""

from __future__ import annotations

import logging
import os
from typing import Any

import anthropic

from .base import AssistantTurn, ToolCall, ToolResult

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"


class AnthropicBackend:
    """Native tool use against the Claude API."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 16000,
        effort: str = "high",
    ) -> None:
        # A bare Anthropic() also picks up an `ant auth login` profile, so only
        # pass a key when we actually have one.
        self.client = (
            anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        )
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort

    @staticmethod
    def available() -> bool:
        return bool(
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        # Stream so a long arranging turn can't hit the request timeout.
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        ) as stream:
            response = stream.get_final_message()

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))

        return AssistantTurn(
            text="\n".join(p for p in text_parts if p.strip()),
            tool_calls=calls,
            raw=response,
        )

    def record_assistant(
        self, messages: list[dict[str, Any]], turn: AssistantTurn
    ) -> None:
        # Echo the content blocks back verbatim -- thinking blocks included.
        messages.append({"role": "assistant", "content": turn.raw.content})

    def record_results(
        self, messages: list[dict[str, Any]], results: list[ToolResult]
    ) -> None:
        # All results for one assistant turn go back in a single user message.
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call_id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )
