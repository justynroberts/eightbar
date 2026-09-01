"""Which choices the user's ears approved, remembered and reused.

Every musical judgement in this codebase is measured except the one that
matters: whether the user liked it. The audition workflow closes that loop --
variants land in session slots, the user listens in Live and names a winner,
and the choice is stored here. From then on, seeded picks (`hooks.pattern_for`,
and anything else that consults this) weight towards what has actually won,
per style, instead of drawing uniformly.

This is deliberately a running tally, not a model. Three wins for
`penta_loop` in house against one for `falling_fifth` means penta_loop is
picked three times as often there -- transparent, inspectable, and wrong in
no surprising ways. A choice can be forgotten; the file can be deleted; the
scores are visible in the tool output.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from .sounds import CONFIG_DIR

TASTE_PATH = CONFIG_DIR / "taste.json"


class Taste:
    """A per-kind, per-context tally of what the user picked."""

    def __init__(self, path: Path = TASTE_PATH) -> None:
        self.path = path
        self._data: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        if self._data is None:
            if self.path.is_file():
                try:
                    self._data = json.loads(self.path.read_text())
                except (OSError, json.JSONDecodeError):
                    self._data = {}
            else:
                self._data = {}
        return self._data

    def save(self) -> None:
        if self._data is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1) + "\n")

    # -- recording ----------------------------------------------------

    def record(self, kind: str, choice: str, context: str = "any") -> dict:
        """One win for `choice`. Context is a style/genre word, or "any"."""
        data = self.load()
        bucket = data.setdefault(kind, {}).setdefault(context.lower(), {})
        bucket[choice] = int(bucket.get(choice, 0)) + 1
        self.save()
        return {"kind": kind, "context": context, "tally": dict(bucket)}

    def forget(self, kind: str, choice: str | None = None,
               context: str = "any") -> dict:
        data = self.load()
        bucket = data.get(kind, {}).get(context.lower(), {})
        if choice is None:
            bucket.clear()
        else:
            bucket.pop(choice, None)
        self.save()
        return {"kind": kind, "context": context, "tally": dict(bucket)}

    # -- consulting ---------------------------------------------------

    def weights(self, kind: str, context: str = "any") -> dict[str, int]:
        """Win counts for a kind, context-specific wins stacked on general."""
        data = self.load()
        table = data.get(kind, {})
        merged: dict[str, int] = dict(table.get("any", {}))
        if context.lower() != "any":
            for choice, count in table.get(context.lower(), {}).items():
                merged[choice] = merged.get(choice, 0) + count * 2
        return merged

    def choose(self, kind: str, options: list[str], context: str = "any",
               seed: int | None = None) -> str:
        """Pick from options, weighted by wins; unweighted when none recorded.

        Every option keeps one baseline vote, so a newcomer is never
        unreachable -- taste narrows the draw, it does not close it.
        """
        if not options:
            raise ValueError("nothing to choose from")
        wins = self.weights(kind, context)
        weighted = [1 + wins.get(option, 0) for option in options]
        rng = random.Random(seed)
        return rng.choices(options, weights=weighted, k=1)[0]

    def summary(self) -> dict:
        data = self.load()
        return {
            kind: {ctx: dict(sorted(tally.items(), key=lambda kv: -kv[1]))
                   for ctx, tally in contexts.items() if tally}
            for kind, contexts in data.items()
        }
