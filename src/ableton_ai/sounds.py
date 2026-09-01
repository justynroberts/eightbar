"""Per-role instrument preferences and favourites.

Generated MIDI is silent until a track has an instrument on it. Rather than
making the model guess -- or asking the user every time -- this stores which
device each musical role should get, so "make me a bassline" can mean "with
Serum, like always".

Stored as JSON at ~/.config/ableton-ai/sounds.json so it survives across
projects and can be edited by hand.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_DIR = Path(
    os.environ.get("ABLETON_AI_CONFIG", Path.home() / ".config" / "ableton-ai")
)
CONFIG_PATH = CONFIG_DIR / "preferences.json"
LEGACY_PATH = CONFIG_DIR / "sounds.json"

# Sensible starting points using only stock Live devices, so the app works
# before the user has expressed any preference.
# Loading "Instruments/Drum Rack" gives an *empty* rack -- a container with no
# samples in any pad, which is silent. Drum roles must point at a real kit.
#
# Which kit suits which genre. The classics are classics for a reason: 909 is
# house, techno and trance; 808 is trap and hip-hop; 707 and 606 are lighter
# and sit better under busy arrangements.
GENRE_KITS: dict[str, str] = {
    "trance": "Drums/909 Core Kit.adg",
    "house": "Drums/909 Core Kit.adg",
    "tech_house": "Drums/909 Core Kit.adg",
    "deep_house": "Drums/707 Core Kit.adg",
    "techno": "Drums/909 Core Kit.adg",
    "melodic_techno": "Drums/909 Core Kit.adg",
    "big_room": "Drums/909 Core Kit.adg",
    "progressive": "Drums/909 Core Kit.adg",
    "dnb": "Drums/606 Core Kit.adg",
    "trap": "Drums/808 Core Kit.adg",
    "hip_hop": "Drums/808 Core Kit.adg",
    "dubstep": "Drums/808 Core Kit.adg",
}
DEFAULT_ROLES: dict[str, str] = {
    "kick": "Drums/909 Core Kit.adg",
    "drums": "Drums/909 Core Kit.adg",
    "perc": "Drums/909 Core Kit.adg",
    "bass": "Instruments/Operator",
    "sub": "Instruments/Operator",
    "chords": "Instruments/Wavetable",
    "arp": "Instruments/Wavetable",
    "lead": "Instruments/Wavetable",
    "hook": "Instruments/Wavetable",
    "pad": "Instruments/Wavetable",
    "riser": "Instruments/Wavetable",
    "impact": "Drums/909 Core Kit.adg",
}


class SoundPreferences:
    """Reads and writes the user's role -> device mapping."""

    def __init__(self, path: Path | None = None) -> None:
        # Resolved at construction, not at import: a default argument binds
        # the module-load value forever, which is how tests that patched
        # CONFIG_PATH still wrote junk into the user's real preferences.
        self.path = path if path is not None else CONFIG_PATH
        self._data: dict[str, Any] | None = None

    # -- persistence --------------------------------------------------

    def load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        data: dict[str, Any] = {"roles": {}, "favourites": [], "rules": []}
        # Preferences used to live in sounds.json; read that if it is all
        # there is, so nothing already saved is lost. Only for the default
        # location -- an explicit path must never fall back to the real user
        # config, or a test (or a second profile) silently inherits it.
        source = self.path
        if not source.is_file() and self.path == CONFIG_PATH:
            source = LEGACY_PATH
        if source.is_file():
            try:
                loaded = json.loads(source.read_text())
                if isinstance(loaded, dict):
                    data["roles"] = dict(loaded.get("roles") or {})
                    data["favourites"] = list(loaded.get("favourites") or [])
                    data["rules"] = list(loaded.get("rules") or [])
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("could not read %s: %s", source, exc)
        self._data = data
        return data

    def save(self) -> None:
        data = self.load()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            log.warning("could not write %s: %s", self.path, exc)

    # -- roles --------------------------------------------------------

    def for_role(self, role: str) -> str | None:
        """The device path for a role: user preference first, then a default."""
        from .arrangement import normalise_role
        role = normalise_role(role) or (role or "").lower()
        roles = self.load()["roles"]
        return roles.get(role) or DEFAULT_ROLES.get(role)

    def set_role(self, role: str, path: str) -> None:
        self.load()["roles"][(role or "").lower()] = path
        self.save()

    def clear_role(self, role: str) -> None:
        self.load()["roles"].pop((role or "").lower(), None)
        self.save()

    # -- favourites ---------------------------------------------------

    def favourites(self) -> list[str]:
        return list(self.load()["favourites"])

    def add_favourite(self, path: str) -> None:
        favourites = self.load()["favourites"]
        if path not in favourites:
            favourites.append(path)
            self.save()

    def remove_favourite(self, path: str) -> None:
        favourites = self.load()["favourites"]
        if path in favourites:
            favourites.remove(path)
            self.save()

    # -- remembered rules ---------------------------------------------

    def rules(self) -> list[str]:
        return list(self.load()["rules"])

    def remember(self, rule: str) -> list[str]:
        """Save a standing instruction, e.g. "always start tracks at 128bpm"."""
        rule = (rule or "").strip()
        if not rule:
            return self.rules()
        rules = self.load()["rules"]
        # Replace a near-duplicate rather than accumulating variations of the
        # same instruction, which would slowly fill the prompt with noise.
        lowered = rule.lower()
        for existing in list(rules):
            if existing.lower() == lowered:
                return list(rules)
        rules.append(rule)
        self.save()
        return list(rules)

    def forget(self, needle: str) -> list[str]:
        """Drop any remembered rule containing `needle`."""
        needle = (needle or "").strip().lower()
        if not needle:
            return self.rules()
        rules = self.load()["rules"]
        kept = [r for r in rules if needle not in r.lower()]
        self.load()["rules"] = kept
        self._data["rules"] = kept  # type: ignore[index]
        self.save()
        return kept

    # -- prompt context ----------------------------------------------

    def describe(self) -> str:
        """A short block for the system prompt, so the model knows the rules."""
        data = self.load()
        blocks: list[str] = []

        sound_lines: list[str] = []
        if data["favourites"]:
            sound_lines.append(
                "Preferred instruments: " + ", ".join(data["favourites"])
            )
        if data["roles"]:
            mapped = ", ".join(f"{k} -> {v}" for k, v in sorted(data["roles"].items()))
            sound_lines.append("Role instruments: " + mapped)
        if sound_lines:
            blocks.append(
                "\n## The user's sound preferences\n\n"
                + "\n".join(sound_lines)
                + "\nUse these when loading instruments unless the user asks for "
                "something else. Call load_sound with just a role and it applies "
                "them automatically.\n"
            )

        if data["rules"]:
            blocks.append(
                "\n## Standing instructions from the user\n\n"
                + "\n".join(f"- {r}" for r in data["rules"])
                + "\nThese were saved deliberately. Follow them without being "
                "asked, and without mentioning them unless they conflict with "
                "what was just requested.\n"
            )

        return "".join(blocks)
