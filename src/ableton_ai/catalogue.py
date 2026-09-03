"""What is actually installed, rather than what was assumed.

The instrument choices used to be a hardcoded table: `bass` meant
`Instruments/Operator`, `chords` meant `Instruments/Wavetable`. That is a guess
about one machine, and it ignores the ~1,400 presets, 147 drum kits and every
third-party plugin actually sitting in the browser. It also silently produced
the wrong thing for anything that is not EDM -- a cinematic string bed does not
come out of Operator.

So the browser is scanned once, cached, and searched by scoring preset names
against what a role and a genre are made of. Nothing here is specific to this
machine: a set-up with different packs installed gets different, equally
sensible answers, and a set-up with none falls back to the stock devices which
always exist.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Where the browser is worth walking. Samples are deliberately excluded from
# the default scan: there are thousands, they are not instruments, and Sampler
# wants a specific file rather than a category.
SCAN_ROOTS: tuple[tuple[str, int], ...] = (
    ("Sounds", 2),              # the preset library, by category
    ("Drums", 1),               # kits, as .adg
    ("Instruments", 2),         # stock devices and their own preset folders
    ("Plugins", 3),             # third-party, vendor/name
)

# What a role is made of, as words that appear in preset names. Order does not
# matter; every hit adds to the score.
ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "kick": ("kick", "bd", "drum", "kit"),
    "drums": ("kit", "drum", "beat", "break", "percussion"),
    "perc": ("perc", "conga", "bongo", "tabla", "shaker", "tambourine", "rim"),
    "snare": ("snare", "clap", "rim", "kit"),
    "hat": ("hat", "hh", "cymbal", "ride", "shaker"),
    "bass": ("bass", "sub", "808", "low", "reese", "wobble"),
    "sub": ("sub", "808", "bass", "sine", "deep"),
    "chords": ("keys", "chord", "stab", "rhodes", "piano", "organ", "poly"),
    "pad": ("pad", "atmos", "ambient", "warm", "evolv", "texture", "drone",
            "swell", "choir", "string"),
    "lead": ("lead", "solo", "saw", "pluck", "synth", "arp", "melody"),
    "hook": ("lead", "pluck", "bell", "synth", "hook", "top"),
    "arp": ("arp", "pluck", "sequence", "seq", "rhythmic", "motion"),
    "piano": ("piano", "grand", "upright", "keys", "rhodes", "wurli", "clav"),
    "strings": ("string", "violin", "cello", "viola", "ensemble", "orch",
                "pizz", "arco", "section"),
    "brass": ("brass", "horn", "trumpet", "trombone", "tuba", "french"),
    "woodwind": ("flute", "clarinet", "oboe", "bassoon", "sax", "wind", "reed"),
    "choir": ("choir", "voice", "vox", "vocal", "aah", "ooh", "chant"),
    "mallet": ("mallet", "marimba", "vibra", "xylo", "glock", "bell", "kalimba"),
    "guitar": ("guitar", "nylon", "acoustic", "strum", "pluck", "banjo", "harp"),
    "harp": ("harp", "pluck", "glissando"),
    "organ": ("organ", "hammond", "church", "leslie"),
    "riser": ("riser", "sweep", "uplift", "noise", "rise", "fx"),
    "impact": ("impact", "hit", "boom", "cinematic", "downlift", "sub drop"),
    "fx": ("fx", "effect", "noise", "texture", "atmos", "sfx"),
    "vocal": ("vocal", "voice", "vox", "choir", "solo"),
}

# What a genre sounds like, in the same terms. These bias the choice within a
# role: a "pad" for cinematic and a "pad" for techno are different presets.
GENRE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cinematic": ("cinematic", "epic", "film", "trailer", "orch", "hybrid",
                  "tension", "dark", "swell", "drama"),
    "orchestral": ("orch", "ensemble", "section", "chamber", "concert",
                   "symphon", "arco", "pizz"),
    "classical": ("piano", "grand", "string", "chamber", "harpsichord",
                  "baroque", "concert", "acoustic"),
    "ambient": ("ambient", "evolv", "drone", "texture", "soft", "air",
                "atmos", "calm", "slow"),
    "jazz": ("jazz", "rhodes", "wurli", "upright", "brush", "swing", "walk",
             "vibra", "sax"),
    "lo_fi": ("tape", "vinyl", "dusty", "lofi", "lo-fi", "warm", "wurli",
              "rhodes", "cassette"),
    "hip_hop": ("808", "trap", "boom", "bap", "dusty", "vinyl", "sub"),
    "trap": ("808", "trap", "sub", "hard", "distort"),
    "house": ("house", "deep", "warm", "analog", "classic", "organ", "disco"),
    "deep_house": ("deep", "warm", "soft", "rhodes", "sub", "dub"),
    "tech_house": ("tech", "tight", "punch", "dry", "stab"),
    "techno": ("techno", "hard", "industrial", "raw", "dark", "hypnotic"),
    "trance": ("trance", "uplift", "supersaw", "epic", "wide", "hoover"),
    "progressive": ("prog", "wide", "deep", "evolv", "melodic", "warm"),
    "dnb": ("dnb", "reese", "neuro", "break", "jungle", "amen"),
    "dubstep": ("wobble", "growl", "neuro", "dub", "heavy", "bass"),
    "pop": ("pop", "bright", "clean", "modern", "vocal", "pluck"),
    "rock": ("guitar", "amp", "drive", "rock", "overdrive", "kit"),
    "folk": ("acoustic", "nylon", "banjo", "mandolin", "fiddle", "warm"),
    "soundtrack": ("cinematic", "film", "score", "tension", "hybrid", "drone"),
}

# The stock devices, which exist on every install and so are always a valid
# last resort. Chosen for what they are good at, not alphabetically.
FALLBACK_DEVICES: dict[str, str] = {
    "kick": "Drums/909 Core Kit.adg",
    "drums": "Drums/909 Core Kit.adg",
    "perc": "Drums/909 Core Kit.adg",
    "snare": "Drums/909 Core Kit.adg",
    "hat": "Drums/909 Core Kit.adg",
    "bass": "Instruments/Operator",
    "sub": "Instruments/Operator",
    "chords": "Instruments/Wavetable",
    "pad": "Instruments/Wavetable",
    "lead": "Instruments/Wavetable",
    "hook": "Instruments/Wavetable",
    "arp": "Instruments/Wavetable",
    "piano": "Instruments/Electric",
    "strings": "Instruments/Tension",
    "mallet": "Instruments/Collision",
    "guitar": "Instruments/Tension",
    "harp": "Instruments/Collision",
    "choir": "Instruments/Wavetable",
    "organ": "Instruments/Analog",
    "riser": "Instruments/Wavetable",
    "impact": "Instruments/Collision",
    "fx": "Instruments/Wavetable",
    "vocal": "Instruments/Sampler",
    "brass": "Instruments/Analog",
    "woodwind": "Instruments/Analog",
}

# Categories under Sounds/ that a role should be searched in first. A match in
# the right category outranks a lucky word elsewhere.
ROLE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "bass": ("Bass",),
    "sub": ("Bass",),
    "pad": ("Pad", "Ambient & Evolving", "Strings"),
    "chords": ("Synth Keys", "Piano & Keys"),
    "lead": ("Synth Lead", "Synth Keys"),
    "hook": ("Synth Lead", "Guitar & Plucked"),
    "arp": ("Synth Rhythmic", "Guitar & Plucked"),
    "piano": ("Piano & Keys",),
    "strings": ("Strings", "Ambient & Evolving"),
    "brass": ("Brass",),
    "woodwind": ("Winds",),
    "choir": ("Voices",),
    "vocal": ("Voices",),
    "mallet": ("Mallets",),
    "guitar": ("Guitar & Plucked",),
    "harp": ("Guitar & Plucked", "Mallets"),
    "organ": ("Piano & Keys", "Synth Keys"),
    "riser": ("Effects", "Ambient & Evolving"),
    "impact": ("Percussive", "Effects"),
    "fx": ("Effects",),
    "perc": ("Percussive",),
}

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


@dataclass
class Entry:
    """One loadable thing in the browser."""

    name: str
    path: str
    root: str
    category: str = ""
    is_device: bool = False

    @property
    def display(self) -> str:
        return self.name.removesuffix(".adg").removesuffix(".adv")


@dataclass
class Catalogue:
    """Everything loadable that the browser holds, scanned once and cached."""

    entries: list[Entry] = field(default_factory=list)
    path: Path | None = None

    # ------------------------------------------------------------- persistence

    @classmethod
    def load(cls, path: Path | None = None) -> "Catalogue":
        from .sounds import CONFIG_DIR

        target = path or (CONFIG_DIR / "catalogue.json")
        found = cls(path=target)
        if target.is_file():
            try:
                raw = json.loads(target.read_text())
            except (OSError, json.JSONDecodeError):
                return found
            found.entries = [Entry(**e) for e in raw.get("entries", [])]
        return found

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"entries": [vars(e) for e in self.entries]}, indent=1
        ) + "\n")

    # ------------------------------------------------------------------ scanning

    def scan(self, bridge, roots: Iterable[tuple[str, int]] = SCAN_ROOTS,
             limit: int = 5000) -> dict:
        """Walk the browser and record everything loadable.

        Depth is per-root because the shapes differ: Sounds is
        category/preset, Plugins is format/vendor/name, and Instruments mixes
        bare devices with their own preset folders.
        """
        self.entries = []
        counts: dict[str, int] = {}

        def walk(path: str, root: str, depth: int, category: str,
                 level: int = 0) -> None:
            try:
                items = bridge.call("browse", path=path, limit=limit)["items"]
            except Exception:                                  # noqa: BLE001
                return
            for item in items:
                child = f"{path}/{item['name']}" if path else item["name"]
                if item.get("is_loadable"):
                    self.entries.append(Entry(
                        name=item["name"], path=child, root=root,
                        category=category,
                        # A bare device sits directly under Instruments; a
                        # preset sits in a category inside it.
                        is_device=(root == "Instruments" and level == 0),
                    ))
                    counts[root] = counts.get(root, 0) + 1
                if item.get("is_folder") and depth > 0:
                    walk(child, root, depth - 1,
                         category or (item["name"] if root == "Sounds" else ""),
                         level + 1)

        for root, depth in roots:
            walk(root, root, depth, "")

        self.save()
        return {
            "total": len(self.entries),
            "by_root": counts,
            "path": str(self.path) if self.path else None,
        }

    # ------------------------------------------------------------------ searching

    def score(self, entry: Entry, role: str, genre: str | None = None) -> float:
        """How well one entry suits a role, and optionally a genre.

        The category a preset lives in is worth more than any single word in
        its name: "Bass" under Sounds/Bass is a bass, while "Bass Drum Pad" in
        Sounds/Pad is not what a bassline wants.
        """
        role_words = ROLE_KEYWORDS.get(role, (role,))
        words = _tokens(entry.display)

        score = 0.0
        if entry.category and entry.category in ROLE_CATEGORIES.get(role, ()):
            # First-choice category. Later choices in the tuple count for less.
            rank = ROLE_CATEGORIES[role].index(entry.category)
            score += 6.0 - rank
        score += sum(2.0 for word in role_words if word in words)
        score += sum(1.0 for word in role_words
                     if word not in words and word in entry.display.lower())

        if genre:
            for word in GENRE_KEYWORDS.get(genre, ()):
                if word in entry.display.lower():
                    score += 1.5

        # A drum kit is a kit whatever else its name says, and a non-drum role
        # never wants one.
        kit = entry.root == "Drums" or "kit" in words
        if role in ("kick", "drums", "perc", "snare", "hat"):
            score += 5.0 if kit else -4.0
        elif kit:
            score -= 5.0

        # Prefer a real preset over a bare device: an empty Wavetable makes a
        # sound, but not one anybody chose.
        if entry.is_device:
            score -= 1.5
        # Prefer Ableton stock over third-party plugins. A stock preset from
        # Sounds/ has a real, named patch the catalogue scanned and scored; a
        # plugin like Serum loads its blank init patch with no name to work
        # from, so it is never a *better* answer than a designed stock preset.
        if entry.root == "Plugins":
            score -= 4.0
        return score

    def find(self, role: str, genre: str | None = None, limit: int = 5,
             prefer: str | None = None) -> list[Entry]:
        """The best matches for a role, best first."""
        if not self.entries:
            return []
        candidates = self.entries
        if prefer:
            wanted = prefer.lower()
            narrowed = [e for e in candidates if wanted in e.path.lower()]
            candidates = narrowed or candidates
        ranked = sorted(
            candidates, key=lambda e: (-self.score(e, role, genre), e.display)
        )
        return [e for e in ranked[:limit] if self.score(e, role, genre) > 0]

    def best(self, role: str, genre: str | None = None,
             seed: int | None = None, variety: int = 8) -> str:
        """A good match for a role -- not always the single top score.

        Ableton ships hundreds of presets per category, so always returning
        the #1 match means every trance bass is the same "Sub 808 Bass". This
        draws from the top `variety` candidates, weighted towards the better
        scores, so a set gets a spread of the stock library instead of one
        preset repeated. Deterministic per seed.
        """
        found = self.find(role, genre, limit=variety)
        if not found:
            return FALLBACK_DEVICES.get(role, "Instruments/Wavetable")
        if len(found) == 1 or seed is None:
            return found[0].path
        # Weight by rank: the top match is likeliest but not certain. A simple
        # descending weight keeps good picks common and poor ones rare.
        import random

        weights = [max(1, variety - i) for i in range(len(found))]
        return random.Random(seed).choices(found, weights=weights, k=1)[0].path

    # ------------------------------------------------------------------ reporting

    def summary(self) -> dict[str, Any]:
        by_root: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for entry in self.entries:
            by_root[entry.root] = by_root.get(entry.root, 0) + 1
            if entry.category:
                by_category[entry.category] = by_category.get(entry.category, 0) + 1
        return {
            "total": len(self.entries),
            "by_root": dict(sorted(by_root.items(), key=lambda kv: -kv[1])),
            "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
            "devices": sorted(e.display for e in self.entries if e.is_device),
            "plugins": sorted(
                e.display for e in self.entries if e.root == "Plugins"
            )[:40],
        }
