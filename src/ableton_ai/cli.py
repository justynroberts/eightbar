"""Terminal chat client, plus connection diagnostics."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .agent import Agent
from .bridge import DEFAULT_PORT, AbletonBridge
from .llm import make_backend

BANNER = """\
ableton-ai -- natural language control of Ableton Live
Type a request, or /help for commands. Ctrl-D to quit.
"""

HELP = """\
/state      show the current Live set
/arrange    show the arrangement timeline
/tools      list available tools
/reset      clear the conversation
/quit       exit
"""


def check(bridge: AbletonBridge) -> int:
    """Report whether Live and the remote script are reachable."""
    print(f"Probing Ableton on {bridge.host}:{bridge.port} ...")
    try:
        info = bridge.call("ping")
    except Exception as exc:
        print(f"  NOT CONNECTED\n  {exc}")
        return 1
    print(f"  connected. Live {info.get('live_version', '?')}, "
          f"protocol {info.get('protocol')}, tempo {info.get('tempo')}")
    print(f"  {len(info.get('commands', []))} commands available")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ableton-ai", description=__doc__)
    parser.add_argument("--check", action="store_true", help="test the Live connection")
    parser.add_argument("--backend", default="auto",
                        help="auto (default), anthropic, or claude-cli")
    parser.add_argument("--model", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("prompt", nargs="*", help="run one request and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    bridge = AbletonBridge(host=args.host, port=args.port)
    if args.check:
        return check(bridge)

    try:
        backend = make_backend(args.backend, model=args.model)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    agent = Agent(backend=backend, bridge=bridge)

    if args.prompt:
        run_turn(agent, " ".join(args.prompt))
        return 0

    print(BANNER)
    print(f"backend: {backend.name}")
    if not bridge.is_available():
        print(
            f"warning: no remote script on port {args.port}. "
            "Run install_remote_script.py, restart Live, and enable AbletonAI "
            "as a Control Surface."
        )

    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return 0
        if line == "/help":
            print(HELP)
            continue
        if line == "/reset":
            agent.reset()
            print("conversation cleared")
            continue
        if line == "/tools":
            for schema in agent.tools:
                print(f"  {schema['name']}")
            continue
        if line in ("/state", "/arrange"):
            command = "get_song_state" if line == "/state" else "get_arrangement"
            try:
                print(json.dumps(agent.toolbox.call(command, {}), indent=2)[:4000])
            except Exception as exc:
                print(f"error: {exc}")
            continue
        run_turn(agent, line)


def run_turn(agent: Agent, message: str) -> None:
    for event in agent.run(message):
        if event.kind == "text":
            print(f"\n{event.data['text']}")
        elif event.kind == "tool_start":
            arguments = json.dumps(event.data["input"])
            print(f"  . {event.data['name']}({arguments[:110]})")
        elif event.kind == "tool_end":
            if event.data["ok"]:
                result = event.data.get("result")
                detail = ""
                if isinstance(result, dict):
                    detail = result.get("summary") or result.get("name") or ""
                print(f"  + {event.data['name']} {detail}".rstrip())
            else:
                print(f"  ! {event.data['name']}: {event.data['error']}")
        elif event.kind == "error":
            print(f"\nerror: {event.data['message']}")


if __name__ == "__main__":
    raise SystemExit(main())
