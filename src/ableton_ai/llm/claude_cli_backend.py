"""`claude -p` backend -- drives the Claude Code CLI as the model.

Useful for development because it needs no API key: it rides whatever
credentials the local `claude` install already has. The CLI has no custom
tool-use channel, so tools are negotiated as a small JSON protocol in the text
instead. The agent loop above this can't tell the difference.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import Any

from ..schemas import render_for_text_protocol
from .base import AssistantTurn, ToolCall, ToolResult

log = logging.getLogger(__name__)

PROTOCOL = """
You are driving a set of tools by emitting JSON. Reply with a single JSON object
and nothing else -- no prose outside it, no markdown fence:

{"say": "<what to tell the user, or an empty string>",
 "tools": [{"name": "<tool name>", "input": {<arguments>}}]}

Put an empty list in "tools" when you are finished and just want to reply.
Call several tools at once when they do not depend on each other.
"""


class ClaudeCLIBackend:
    """Runs `claude -p` as a one-shot completion engine."""

    name = "claude-cli"

    def __init__(
        self,
        executable: str = "claude",
        model: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self.executable = executable
        self.model = model
        self.timeout = timeout

    @staticmethod
    def available(executable: str = "claude") -> bool:
        return shutil.which(executable) is not None

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        prompt = self._render(system, messages, tools)

        command = [self.executable, "-p", prompt, "--output-format", "json"]
        if self.model:
            command += ["--model", self.model]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"'{self.executable}' not found on PATH. Install Claude Code, or "
                "set ANTHROPIC_API_KEY and use the anthropic backend."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"claude -p timed out after {self.timeout}s") from exc

        if completed.returncode != 0:
            raise RuntimeError(
                f"claude -p failed ({completed.returncode}): "
                f"{completed.stderr.strip()[:500]}"
            )

        payload = self._unwrap(completed.stdout)
        return self._parse(payload)

    # -- prompt construction -----------------------------------------

    def _render(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> str:
        parts = [system, PROTOCOL, "Available tools:", render_for_text_protocol(tools)]
        parts.append("\nConversation so far:")
        for message in messages:
            role = message["role"].upper()
            parts.append(f"\n[{role}]\n{self._flatten(message['content'])}")
        parts.append(
            "\nRespond now with the JSON object described above, and nothing else."
        )
        return "\n".join(parts)

    @staticmethod
    def _flatten(content: Any) -> str:
        if isinstance(content, str):
            return content
        chunks: list[str] = []
        for block in content or []:
            if isinstance(block, str):
                chunks.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    chunks.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    label = "TOOL ERROR" if block.get("is_error") else "TOOL RESULT"
                    chunks.append(f"[{label}] {block.get('content')}")
                else:
                    chunks.append(json.dumps(block)[:2000])
        return "\n".join(c for c in chunks if c)

    # -- response parsing --------------------------------------------

    @staticmethod
    def _unwrap(stdout: str) -> str:
        """`--output-format json` wraps the answer in an envelope; unwrap it."""
        text = stdout.strip()
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(envelope, dict):
            for key in ("result", "text", "content", "response"):
                value = envelope.get(key)
                if isinstance(value, str):
                    return value
        return text

    def _parse(self, text: str) -> AssistantTurn:
        payload = self._extract_json(text)
        if payload is None:
            # No protocol object -- treat the whole thing as a plain reply.
            return AssistantTurn(text=text.strip(), tool_calls=[], raw=text)

        calls: list[ToolCall] = []
        for index, entry in enumerate(payload.get("tools") or []):
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            calls.append(
                ToolCall(
                    id=f"cli_{index}",
                    name=str(entry["name"]),
                    input=dict(entry.get("input") or {}),
                )
            )
        return AssistantTurn(
            text=str(payload.get("say") or "").strip(), tool_calls=calls, raw=text
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """Find the protocol object, tolerating fences and surrounding prose."""
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        candidates = [fenced.group(1)] if fenced else []
        candidates.append(text.strip())

        # Fall back to the widest brace-balanced span in the output.
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[start : i + 1])
                        break

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and ("tools" in parsed or "say" in parsed):
                return parsed
        return None

    # -- transcript bookkeeping --------------------------------------

    def record_assistant(
        self, messages: list[dict[str, Any]], turn: AssistantTurn
    ) -> None:
        summary = turn.text
        if turn.tool_calls:
            calls = ", ".join(
                f"{c.name}({json.dumps(c.input)[:300]})" for c in turn.tool_calls
            )
            summary = f"{summary}\n[called: {calls}]".strip()
        messages.append({"role": "assistant", "content": summary or "[called tools]"})

    def record_results(
        self, messages: list[dict[str, Any]], results: list[ToolResult]
    ) -> None:
        lines = []
        for result in results:
            label = "ERROR" if result.is_error else "OK"
            lines.append(f"[{result.name} -> {label}] {result.content}")
        messages.append({"role": "user", "content": "\n".join(lines)})
