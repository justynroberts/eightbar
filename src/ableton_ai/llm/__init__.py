"""Pluggable LLM backends."""

from __future__ import annotations

import logging
import os

from .anthropic_backend import AnthropicBackend
from .base import AssistantTurn, Backend, ToolCall, ToolResult
from .claude_cli_backend import ClaudeCLIBackend

log = logging.getLogger(__name__)

__all__ = [
    "AnthropicBackend",
    "AssistantTurn",
    "Backend",
    "ClaudeCLIBackend",
    "ToolCall",
    "ToolResult",
    "make_backend",
]


def make_backend(
    kind: str = "auto", model: str | None = None, api_key: str | None = None
) -> Backend:
    """Pick a backend.

    "auto" prefers the API when a key is present and falls back to `claude -p`,
    which is what makes the app usable for testing before a key is supplied.
    """
    kind = (kind or "auto").lower()

    if kind in ("anthropic", "api"):
        return AnthropicBackend(api_key=api_key, model=model or "claude-opus-5")
    if kind in ("claude-cli", "cli", "claude", "p"):
        return ClaudeCLIBackend(model=model)

    if kind != "auto":
        raise ValueError(f"unknown backend {kind!r}; use auto, anthropic or claude-cli")

    if api_key or AnthropicBackend.available():
        return AnthropicBackend(api_key=api_key, model=model or "claude-opus-5")
    if ClaudeCLIBackend.available():
        log.info("No ANTHROPIC_API_KEY found -- using the claude CLI backend.")
        return ClaudeCLIBackend(model=model)

    raise RuntimeError(
        "No LLM backend available. Either set ANTHROPIC_API_KEY, or install "
        "Claude Code so `claude -p` can be used."
    )
