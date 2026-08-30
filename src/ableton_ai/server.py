"""FastAPI server: static UI plus a streaming chat endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import Agent
from .bridge import DEFAULT_PORT, AbletonBridge
from .llm import make_backend

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(title="Ableton AI", version="0.1.0")

# One agent per process: the conversation and the Live connection are shared by
# whichever browser tab is driving. This is a single-user local tool.
_state: dict[str, Any] = {"agent": None, "error": None}


class ChatRequest(BaseModel):
    message: str


def get_agent() -> Agent:
    if _state["agent"] is None:
        bridge = AbletonBridge(
            host=os.environ.get("ABLETON_HOST", "127.0.0.1"),
            port=int(os.environ.get("ABLETON_PORT", DEFAULT_PORT)),
        )
        backend = make_backend(
            os.environ.get("ABLETON_AI_BACKEND", "auto"),
            model=os.environ.get("ABLETON_AI_MODEL"),
        )
        _state["agent"] = Agent(backend=backend, bridge=bridge)
    return _state["agent"]


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


@app.get("/api/status")
def status() -> dict[str, Any]:
    """Whether Live and a model backend are both reachable."""
    try:
        agent = get_agent()
    except Exception as exc:
        return {"backend": None, "ableton": False, "error": str(exc)}

    connected = agent.bridge.is_available()
    payload: dict[str, Any] = {
        "backend": agent.backend.name,
        "ableton": connected,
        "port": agent.bridge.port,
        "tools": len(agent.tools),
    }
    if connected:
        try:
            payload["song"] = agent.toolbox.call("get_song_state", {})
        except Exception as exc:
            payload["error"] = str(exc)
    return payload


@app.get("/api/song")
def song() -> dict[str, Any]:
    try:
        return get_agent().toolbox.call("get_song_state", {})
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/arrangement")
def arrangement() -> dict[str, Any]:
    try:
        return get_agent().toolbox.call("get_arrangement", {})
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/tools")
def tools() -> dict[str, Any]:
    agent = get_agent()
    return {
        "tools": [
            {"name": t["name"], "description": t["description"].split("\n")[0]}
            for t in agent.tools
        ]
    }


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    get_agent().reset()
    return {"ok": True}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Server-sent events, one JSON object per agent step."""
    agent = get_agent()

    async def stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def produce() -> None:
            # The agent loop is blocking (sockets + HTTP), so it runs off-thread.
            try:
                for event in agent.run(request.message):
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"kind": event.kind, **{k: _jsonable(v)
                                                for k, v in event.data.items()}},
                    )
            except Exception as exc:
                log.exception("agent failed")
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"kind": "error", "message": str(exc)}
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        await loop.run_in_executor(None, lambda: None)
        task = loop.run_in_executor(None, produce)

        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, default=str)}\n\n"
        await task
        yield "data: {\"kind\": \"end\"}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def main() -> None:
    import uvicorn

    # Non-standard port, per the house rules on avoiding 3000/8000 collisions.
    port = int(os.environ.get("ABLETON_AI_UI_PORT", 7817))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
