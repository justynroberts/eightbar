"""TCP client for the AbletonAI remote script running inside Live.

Framing is newline-delimited JSON, one request and one response per line, so a
slow reply can never bleed into the next command's buffer.
"""

from __future__ import annotations

import itertools
import json
import logging
import socket
import threading
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9878


class AbletonError(RuntimeError):
    """Live reached, but the command failed."""


class AbletonNotRunning(RuntimeError):
    """Could not reach the remote script at all."""


class AbletonBridge:
    """A reconnecting, thread-safe JSON-RPC-ish client for Live."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buffer = b""
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # -- connection ---------------------------------------------------

    def connect(self) -> None:
        if self._sock is not None:
            return
        try:
            sock = socket.create_connection((self.host, self.port), timeout=5.0)
        except OSError as exc:
            raise AbletonNotRunning(
                f"No AbletonAI remote script on {self.host}:{self.port}. "
                "Start Ableton Live and select 'AbletonAI' as a Control Surface "
                "in Preferences > Link, Tempo & MIDI."
            ) from exc
        sock.settimeout(self.timeout)
        self._sock = sock
        self._buffer = b""
        log.info("Connected to Ableton on %s:%s", self.host, self.port)

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buffer = b""

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def is_available(self) -> bool:
        """Cheap reachability probe that never raises."""
        try:
            self.call("ping")
            return True
        except (AbletonNotRunning, AbletonError, OSError):
            return False

    # -- request/response --------------------------------------------

    def call(self, command: str, **params: Any) -> dict[str, Any]:
        """Send one command and return its `result` payload."""
        with self._lock:
            try:
                return self._call_locked(command, params)
            except (OSError, AbletonNotRunning):
                # A stale socket survives an Ableton restart; retry once clean.
                self._close_locked()
                return self._call_locked(command, params)

    def _call_locked(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        self.connect()
        assert self._sock is not None

        request_id = next(self._ids)
        payload = json.dumps(
            {"id": request_id, "type": command, "params": params}
        ).encode("utf-8")
        self._sock.sendall(payload + b"\n")

        response = self._read_line()
        if response.get("status") == "error":
            raise AbletonError(f"{command}: {response.get('message', 'unknown error')}")
        return response.get("result", {})

    def _read_line(self) -> dict[str, Any]:
        assert self._sock is not None
        while b"\n" not in self._buffer:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout as exc:
                raise AbletonError(
                    f"Ableton did not respond within {self.timeout}s"
                ) from exc
            if not chunk:
                self._close_locked()
                raise AbletonNotRunning("Ableton closed the connection")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    # -- convenience --------------------------------------------------

    def __enter__(self) -> AbletonBridge:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
